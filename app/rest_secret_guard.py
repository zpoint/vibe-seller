"""The REST side of the write-only-secret contract — for EVERY caller.

MCP tool results pass through ``app/agent_mcp/redact.py``, and the
same classifier now guards plain REST for every credential class:
a stored secret is returned to NOBODY as plaintext — not to a
``vst_`` token, not to a member session, not to an admin session,
not to the no-auth default admin. The only way out is the admin
reveal endpoint (``app/routers/secrets.py``), which refuses token
callers and writes a log line per reveal.

THE INVARIANT, enforced here for the whole REST surface: no route
returns a stored secret in a normal response — every caller sees the
same masks, from the same classifier, MCP shows.

Enforcement point: ONE middleware (``SecretGuardMiddleware``), not a
helper each secret-returning route must remember to call. The
invariant is a property of the RESPONSE, not of individual routes —
"ANY route" is structurally true only where every response passes
through, and that is the transport. A per-route helper keeps the
invariant aspirational (true as long as every route author opts in
and a test enumerates them); here a new secret-returning route is
masked without anybody doing anything. The drift guards then PIN
the behaviour, they do not provide it.

The ONE deliberate exception is the route that hands out a FRESH
credential (minting an API token): its creation response carries the
plaintext once, on purpose, because that is the only moment the
caller can ever see it. Such a route marks itself with
:func:`mark_secret_disclosure` — the response-side twin of the
token-scope refusals (``app/api_token_scope.py``) — and the curated
list of those routes is pinned by the drift guard.

Both directions of the contract, at the same enforcement point:

* RESPONSE — when the response is JSON (and not a file delivery),
  the body is walked with the shared classifier
  (:func:`app.agent_mcp.redact.redact`). Only classified field NAMES
  holding STRING values are masked: numeric ``*_tokens`` counts,
  booleans like ``has_secret`` and the routes' own ``*_masked``
  fields pass through (``PLAINTEXT_FIELDS``). Anything not JSON —
  SSE streams, file downloads, HTML — passes untouched.

* REQUEST — the write side. Every caller can only ever hold a MASK
  (nobody receives plaintext any more), so a mask posted back to a
  write route means "keep the stored secret". The same
  ``restore_masked`` the MCP tools apply runs here through a table
  of rules (``RESTORE_RULES``); an EMPTY string means the same
  (``empty_stays`` — an untouched password box posts empty), and a
  mask with NO stored secret is a 400, not a silent store of
  ``'****1234'``.

The rule table is what keeps the request side total:
``tests/unit/test_rest_secret_guard.py`` enumerates every REST route
whose BODY carries a secret-classified field (pydantic body models
introspected from the real app; the plain-dict bodies — profiles
``env``, vision ``kie_api_key`` — listed alongside) and fails when
one of them has no rule here, so a new secret-accepting route cannot
ship without the restore.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import json
import logging
import re

from app.agent_mcp.redact import redact, restore_masked
from app.agent_mcp.support import ToolError
from app.ai.profiles import ProfileManager
from app.api_tokens import TOKEN_PREFIX
from app.database import async_session
from app.models.chat_bot import ChatBot
from app.models.email_account import EmailAccount
from app.models.ziniao_account import ZiniaoAccount
from app.routers.dida365_oauth import load_backend_config
from app.utils.crypto import decrypt_password
from app.vision import get_kie_api_key

logger = logging.getLogger(__name__)

# A stored-secret shape handed to a provider: field name -> the value
# the server currently holds (None when nothing is stored).
StoredMap = dict[str, str | None]
# The provider gets the matched path params AND the parsed request
# body — the one route that needs it is ``POST /api/profiles/validate``,
# whose stored env is named by a body field (``profile_id``), not by
# the path.
StoredProvider = Callable[[dict[str, str], dict], Awaitable[StoredMap]]

_SECRET_DISCLOSURE_STATE = 'discloses_secret'


def mark_secret_disclosure(request) -> None:
    """Flag THIS request's response as a deliberate credential
    disclosure (the token-mint route; the admin reveal endpoint).

    The middleware masks every JSON response for every caller; a
    route that just CREATED (or explicitly revealed) a credential
    and must hand it over once calls this so its own response is not
    masked. The shared ``scope['state']`` dict is read back at
    response time — the same mechanism ``auth_via`` uses.

    Curated by the drift guard: every route that calls this is
    listed in ``SECRET_DISCLOSURE_ROUTES``
    (tests/unit/test_rest_secret_guard.py), and each entry's
    behaviour is pinned by a workflow test.
    """
    setattr(request.state, _SECRET_DISCLOSURE_STATE, True)


def _discloses_secret(scope: dict) -> bool:
    """Whether the app flagged this request as a disclosure."""
    state = scope.get('state') or {}
    return bool(state.get(_SECRET_DISCLOSURE_STATE))


# ── rule table ──────────────────────────────────────────────────


@dataclass(frozen=True)
class RestoreRule:
    """One REST write surface a masked echo can reach.

    * *methods* — the HTTP methods whose bodies carry secrets here;
    * *template* — the route's own path, ``{param}`` for path params,
      compiled to a regex (``[^/]+`` per param) at table-build time;
    * *stored* — async provider returning the values the server
      currently holds for the matched resource, read through the same
      manager/table the route itself reads. ``restore_masked`` walks
      the incoming body against it.
    """

    methods: frozenset[str]
    template: str
    stored: StoredProvider
    pattern: 're.Pattern[str]'

    def matches(self, method: str, path: str) -> 're.Match[str] | None':
        if method not in self.methods:
            return None
        return self.pattern.fullmatch(path)


def _rule(
    methods: set[str], template: str, stored: StoredProvider
) -> RestoreRule:
    return RestoreRule(
        methods=frozenset(methods),
        template=template,
        stored=stored,
        pattern=re.compile(compile_template(template)),
    )


def compile_template(template: str) -> str:
    """A regex that full-matches the route's own path shape, with the
    path params as NAMED groups — ``match.groupdict()`` is what the
    stored-value providers key on."""
    parts = re.split(r'(\{[^/]+\})', template)
    out = []
    for part in parts:
        param = re.fullmatch(r'\{([^/]+)\}', part)
        if param is None:
            out.append(re.escape(part))
        else:
            out.append(f'(?P<{param.group(1)}>[^/]+)')
    return ''.join(out)


async def _nothing(match: dict[str, str], body: dict) -> StoredMap:
    """No restorable stored value — a mask here can only be an error.

    Create-style bodies, and secrets stored only as a one-way hash
    (login/user passwords): the caller cannot have read a mask of
    these from us, and there is no plaintext to restore to.
    """
    return {}


async def _stored_profile_env(match: dict[str, str], body: dict) -> StoredMap:
    """The env a profile stores, via the same manager the route uses."""
    profile = ProfileManager.get_profile(match['profile_id'])
    return {'env': (profile or {}).get('env') or {}}


async def _stored_profile_env_by_body(
    match: dict[str, str], body: dict
) -> StoredMap:
    """The env of the profile a validate probe names in its body.

    ``POST /api/profiles/validate`` takes no profile id in its path;
    the Settings form sends the id it is about to save, so a masked
    key in the probe env is restored against THAT profile's stored
    env before the probe runs. No id (create flow) → nothing stored.
    """
    profile_id = body.get('profile_id') or ''
    profile = ProfileManager.get_profile(profile_id) if profile_id else None
    return {'env': (profile or {}).get('env') or {}}


async def _stored_chat_bot(match: dict[str, str], body: dict) -> StoredMap:
    """The stored webhook URL + signing secret of one bot."""
    async with async_session() as db:
        bot = await db.get(ChatBot, match['bot_id'])
    if bot is None:
        return {}
    return {'webhook_url': bot.webhook_url, 'secret': bot.secret}


async def _stored_vision_key(match: dict[str, str], body: dict) -> StoredMap:
    """The stored kie.ai key."""
    return {'kie_api_key': get_kie_api_key()}


async def _stored_email_password(
    match: dict[str, str], body: dict
) -> StoredMap:
    """The stored (decrypted) email account password."""
    async with async_session() as db:
        account = await db.get(EmailAccount, match['account_id'])
    if account is None or not account.encrypted_password:
        return {}
    return {'password': decrypt_password(account.encrypted_password)}


async def _stored_ziniao_password(
    match: dict[str, str], body: dict
) -> StoredMap:
    """The stored (decrypted) Ziniao account password."""
    async with async_session() as db:
        account = await db.get(ZiniaoAccount, match['account_id'])
    if account is None or not account.encrypted_password:
        return {}
    return {'password': decrypt_password(account.encrypted_password)}


async def _stored_dida_secret(match: dict[str, str], body: dict) -> StoredMap:
    """The stored (decrypted) Dida365 OAuth client secret."""
    enc = load_backend_config('dida365').get('client_secret_enc')
    return {'client_secret': decrypt_password(enc) if enc else None}


RESTORE_RULES: tuple[RestoreRule, ...] = (
    # AI profiles — the env dict carries the provider credentials.
    _rule({'POST'}, '/api/profiles', _nothing),
    _rule({'PUT'}, '/api/profiles/{profile_id}', _stored_profile_env),
    # validate probes an env it does not persist; the id of the
    # profile being edited (if any) arrives in the body, and a masked
    # key is restored against that profile so an untouched key probes
    # the stored secret instead of the mask.
    _rule({'POST'}, '/api/profiles/validate', _stored_profile_env_by_body),
    # Chat bots — the webhook URL embeds the key; the secret signs.
    _rule({'POST'}, '/api/chat-bots', _nothing),
    _rule({'PUT'}, '/api/chat-bots/{bot_id}', _stored_chat_bot),
    # Vision — the kie.ai key.
    _rule({'PUT'}, '/api/vision/config', _stored_vision_key),
    # Email accounts — IMAP/SMTP password. The /test route probes
    # raw credentials before any account exists to restore from.
    _rule({'POST'}, '/api/email-accounts', _nothing),
    _rule({'POST'}, '/api/email-accounts/test', _nothing),
    _rule(
        {'PUT'},
        '/api/email-accounts/{account_id}',
        _stored_email_password,
    ),
    # Ziniao accounts — client login password.
    _rule({'POST'}, '/api/ziniao-accounts', _nothing),
    _rule(
        {'PUT'},
        '/api/ziniao-accounts/{account_id}',
        _stored_ziniao_password,
    ),
    # User management — passwords are stored hashed only, so a mask
    # is never an echo of something we handed out; refusing it keeps
    # the write guard total without inventing a restore.
    _rule({'POST'}, '/api/users', _nothing),
    _rule({'PUT'}, '/api/users/{user_id}', _nothing),
    _rule({'POST'}, '/api/auth/login', _nothing),
    _rule({'PATCH'}, '/api/auth/me/password', _nothing),
    # Dida365/TickTick OAuth client secret.
    _rule({'POST'}, '/api/dida365/authorize', _stored_dida_secret),
)


def matching_restore_rule(method: str, path: str) -> RestoreRule | None:
    """The first rule whose (method, path) this request matches."""
    for rule in RESTORE_RULES:
        if rule.matches(method, path):
            return rule
    return None


# ── scope helpers ───────────────────────────────────────────────

_COOKIE_NAME = b'auth_token'
_BEARER = b'Bearer '


def _headers(scope: dict) -> dict[bytes, bytes]:
    return {key: value for key, value in scope.get('headers') or []}


def _presented_api_token(scope: dict) -> bool:
    """Whether this request presents a ``vst_`` credential at all.

    The route resolves the token against the DB later (a bad token
    401s there); for deciding whether the restore or scope contract
    applies, the presentation is enough — the same signal
    ``get_current_user`` keys on (Bearer header first, then the
    session cookie). Imported by ``app/api_token_scope.py`` for the
    token-scope refusal table.
    """
    headers = _headers(scope)
    auth = headers.get(b'authorization', b'')
    if auth.startswith(_BEARER) and auth[len(_BEARER) :].startswith(
        TOKEN_PREFIX.encode()
    ):
        return True
    for part in headers.get(b'cookie', b'').split(b';'):
        name, _, value = part.strip().partition(b'=')
        if name == _COOKIE_NAME and value.startswith(TOKEN_PREFIX.encode()):
            return True
    return False


def _is_json_type(media_type: bytes | str) -> bool:
    """``application/json`` or any ``+json`` structured suffix."""
    if isinstance(media_type, bytes):
        media_type = media_type.decode('latin-1')
    media_type = media_type.split(';', 1)[0].strip().lower()
    return media_type == 'application/json' or media_type.endswith('+json')


async def _send_json(send, status: int, payload: dict) -> None:
    """Answer the request ourselves (the restore-refusal 400)."""
    body = json.dumps(payload).encode('utf-8')
    await send({
        'type': 'http.response.start',
        'status': status,
        'headers': [
            (b'content-type', b'application/json'),
            (b'content-length', str(len(body)).encode('ascii')),
        ],
    })
    await send({'type': 'http.response.body', 'body': body})


def _with_content_length(
    headers: list[tuple[bytes, bytes]], length: int
) -> list[tuple[bytes, bytes]]:
    """A copy of *headers* whose content-length says *length*."""
    out = [(key, value) for key, value in headers if key != b'content-length']
    out.append((b'content-length', str(length).encode('ascii')))
    return out


def _set_request_content_length(scope: dict, length: int) -> None:
    """Keep the request's declared content-length honest when the
    restore rewrote the body (a mask is shorter than the secret)."""
    headers = [
        (key, value)
        for key, value in scope.get('headers') or []
        if key != b'content-length'
    ]
    headers.append((b'content-length', str(length).encode('ascii')))
    scope['headers'] = headers


# ── the middleware ──────────────────────────────────────────────


class SecretGuardMiddleware:
    """Pure ASGI (the request side must rebuild ``receive`` before the
    multipart/JSON parsers run, exactly like
    ``UploadBodyLimitMiddleware``). Registered innermost, inside the
    body limit and CORS, so its 400 reaches cross-origin callers as a
    readable status like the 413 does.
    """

    def __init__(self, app, rules: tuple[RestoreRule, ...] = RESTORE_RULES):
        self.app = app
        self.rules = rules

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        rule = self._matching_rule(scope)
        if rule is None:
            await self._run(scope, receive, send)
            return

        parsed, raw = await self._read_json_body(receive)
        payload = raw  # what the app receives when no restore applies
        if parsed is not None:
            try:
                match = rule.pattern.fullmatch(scope['path'])
                stored = await rule.stored(
                    match.groupdict() if match else {}, parsed
                )
                payload = json.dumps(
                    restore_masked(parsed, stored, empty_stays=True)
                ).encode('utf-8')
            except ToolError as exc:
                logger.info(
                    'Refused masked secret on %s %s: %s',
                    scope['method'],
                    scope['path'],
                    exc,
                )
                await _send_json(send, 400, {'detail': str(exc)})
                return

        async def replay():
            return {
                'type': 'http.request',
                'body': payload,
                'more_body': False,
            }

        if len(payload) != len(raw):
            _set_request_content_length(scope, len(payload))
        await self._run(scope, replay, send)

    def _matching_rule(self, scope: dict) -> RestoreRule | None:
        method = scope['method'].upper()
        for rule in self.rules:
            if rule.matches(method, scope['path']):
                return rule
        return None

    @staticmethod
    async def _read_json_body(receive) -> tuple[dict | None, bytes]:
        """The request body as (parsed JSON or None, raw bytes).

        The bytes are ALWAYS captured because consuming them from
        ``receive`` is irreversible: whatever happens next, the app
        must be fed what arrived. ``None`` covers every case the
        restore contract does not apply to — empty bodies, non-JSON
        content, malformed JSON, and a client that disconnected
        mid-body.
        """
        chunks: list[bytes] = []
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return None, b''.join(chunks)
            if message['type'] != 'http.request':
                continue
            chunks.append(message.get('body', b''))
            if not message.get('more_body', False):
                break
        raw = b''.join(chunks)
        if not raw:
            return None, raw
        try:
            return json.loads(raw), raw
        except ValueError:
            return None, raw

    async def _run(self, scope, receive, send):
        """Call the app with a send that redacts JSON responses for
        every caller (unless the route flagged a deliberate
        disclosure). Everything else passes through as it came.
        """
        buffering = False
        started: dict | None = None
        chunks: list[bytes] = []

        async def send_wrapper(message):
            nonlocal buffering, started
            if message['type'] == 'http.response.start':
                headers = dict(message.get('headers') or [])
                buffering = (
                    not _discloses_secret(scope)
                    and _is_json_type(headers.get(b'content-type', b''))
                    # A content-disposition marks a FILE delivery
                    # (every download route passes filename=), not an
                    # API payload: a task's config.json output must
                    # arrive byte-for-byte, not walked because its
                    # extension picked a JSON media type.
                    and b'content-disposition' not in headers
                )
                if buffering:
                    started = {
                        'status': message['status'],
                        'headers': list(message.get('headers') or []),
                    }
                    return  # held until the body is complete
                await send(message)
                return
            if message['type'] != 'http.response.body':
                await send(message)
                return
            if not buffering:
                await send(message)
                return
            chunks.append(message.get('body', b''))
            if message.get('more_body', False):
                return

            body = b''.join(chunks)
            out = body
            if body:
                try:
                    data = json.loads(body)
                except ValueError:
                    logger.warning(
                        'Unparsable JSON body on %s %s despite its '
                        'content-type; passing it through unredacted',
                        scope['method'],
                        scope['path'],
                    )
                else:
                    out = json.dumps(redact(data)).encode('utf-8')
            await send({
                'type': 'http.response.start',
                'status': started['status'],
                'headers': _with_content_length(started['headers'], len(out)),
            })
            await send({'type': 'http.response.body', 'body': out})

        await self.app(scope, receive, send_wrapper)
