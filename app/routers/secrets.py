"""The one route a stored secret ever leaves the server through.

Everything else is masked for every caller
(``app/rest_secret_guard.py``); ``POST /api/secrets/reveal`` is the
admin's explicit "Show / Copy full key" action behind the edit
forms. One endpoint shape covers every secret-bearing resource:

* ``profile``         — one secret-classified key of a profile's env
* ``chat_bot``        — ``webhook_url`` / ``secret``
* ``email_account``   — ``password`` (decrypted)
* ``ziniao_account``  — ``password`` (decrypted)
* ``vision``          — ``kie_api_key``
* ``dida365``         — ``client_secret``

Body ``{resource, id, field}`` (``id`` unused for the singleton
resources) → ``{resource, id, field, value}``.

Access, in order:

* admin session/no-auth admin only — members get 403 whatever their
  credential class, because reading a stored secret is exactly the
  capability members must never gain (they may OVERWRITE one; the
  write path never reads the old value back to them);
* ANY ``vst_`` API-token caller is refused (403), an admin's token
  included — a token must never read secrets, on MCP or REST, and a
  reveal endpoint is the one place that invariant would leak first.
  Enforced as a row in ``TOKEN_REFUSALS`` (``app/api_token_scope.py``),
  so the scope middleware answers it before this route runs.

Every successful reveal writes ONE line to the server log — who,
with what role, through which credential class, what resource and
field — and NEVER the value itself:

    secret revealed: user=alice role=admin resource=profile:minimax \
field=ANTHROPIC_AUTH_TOKEN via=session

Unknown resource, unknown field, a resource id that does not exist,
or a field with nothing stored are all 404 (never 403 — existence is
not a secret from an admin). The response is ``Cache-Control:
no-store`` and flagged as a deliberate disclosure so the guard
middleware lets it through untouched.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.agent_mcp.redact import is_secret_key
from app.ai.profiles import ProfileManager
from app.auth import require_admin
from app.database import async_session
from app.models.chat_bot import ChatBot
from app.models.email_account import EmailAccount
from app.models.user import User
from app.models.ziniao_account import ZiniaoAccount
from app.rest_secret_guard import mark_secret_disclosure
from app.routers.dida365_oauth import load_backend_config
from app.utils.crypto import decrypt_password
from app.vision import get_kie_api_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/api/secrets', tags=['secrets'])


async def _reveal_profile(resource_id: str, field: str):
    """One secret-classified env key of a stored profile.

    ``field`` is the env KEY (e.g. ``ANTHROPIC_AUTH_TOKEN``). A key
    that is not secret-classified is 404 too — those are already
    returned in plaintext by the masked profile list, so "revealing"
    one would only write a misleading audit line.
    """
    profile = ProfileManager.get_profile(resource_id)
    if profile is None:
        return None
    env = profile.get('env') or {}
    if field not in env or not is_secret_key(field):
        return None
    value = env.get(field)
    return value if value else None


async def _reveal_chat_bot(resource_id: str, field: str):
    if field not in ('webhook_url', 'secret'):
        return None
    async with async_session() as db:
        bot = await db.get(ChatBot, resource_id)
    if bot is None:
        return None
    value = getattr(bot, field)
    return value if value else None


async def _reveal_email_password(resource_id: str, field: str):
    if field != 'password':
        return None
    async with async_session() as db:
        account = await db.get(EmailAccount, resource_id)
    if account is None or not account.encrypted_password:
        return None
    return decrypt_password(account.encrypted_password)


async def _reveal_ziniao_password(resource_id: str, field: str):
    if field != 'password':
        return None
    async with async_session() as db:
        account = await db.get(ZiniaoAccount, resource_id)
    if account is None or not account.encrypted_password:
        return None
    return decrypt_password(account.encrypted_password)


async def _reveal_vision_key(_resource_id: str, field: str):
    if field != 'kie_api_key':
        return None
    key = get_kie_api_key()
    return key or None


async def _reveal_dida_secret(_resource_id: str, field: str):
    if field != 'client_secret':
        return None
    enc = load_backend_config('dida365').get('client_secret_enc')
    return decrypt_password(enc) if enc else None


# resource name -> (needs an id, resolver). One shape for every
# secret-bearing resource the server stores.
REVEAL_RESOURCES: dict[str, tuple[bool, object]] = {
    'profile': (True, _reveal_profile),
    'chat_bot': (True, _reveal_chat_bot),
    'email_account': (True, _reveal_email_password),
    'ziniao_account': (True, _reveal_ziniao_password),
    'vision': (False, _reveal_vision_key),
    'dida365': (False, _reveal_dida_secret),
}


@router.post('/reveal')
async def reveal_secret(
    body: dict,
    request: Request,
    user: User = Depends(require_admin),
):
    """Return one stored secret to an admin (see module docstring)."""
    # require_admin (the dependency) already resolved the caller and
    # their role. The credential-class refusal — 403 for ANY ``vst_``
    # token caller, an admin's token included — is a row in
    # ``TOKEN_REFUSALS`` (``app/api_token_scope.py``), answered by
    # ``ApiTokenScopeMiddleware`` before this route ever runs, so a
    # token learns nothing about which resources exist.

    resource = (body.get('resource') or '').strip()
    resource_id = (body.get('id') or '').strip()
    field = (body.get('field') or '').strip()

    entry = REVEAL_RESOURCES.get(resource)
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail=f'Unknown secret resource: {resource or "(none)"}',
        )
    needs_id, resolver = entry
    if needs_id and not resource_id:
        raise HTTPException(
            status_code=404, detail=f'{resource} requires an id'
        )

    value = await resolver(resource_id, field)
    if value is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f'No stored {resource} secret for field {field or "(none)"}'
            ),
        )

    # The audit line — WHO read WHAT, never the value itself.
    where = f'{resource}:{resource_id}' if resource_id else resource
    logger.info(
        'secret revealed: user=%s role=%s resource=%s field=%s via=%s',
        user.username,
        user.role,
        where,
        field,
        getattr(request.state, 'auth_via', None),
    )

    mark_secret_disclosure(request)
    return JSONResponse(
        content={
            'resource': resource,
            'id': resource_id,
            'field': field,
            'value': value,
        },
        headers={'Cache-Control': 'no-store'},
    )
