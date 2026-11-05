"""The configuration tools: ``manage_ziniao_account``,
``manage_profile`` and ``app_settings``.

Thin adapters over ``app/routers/ziniao_accounts.py``,
``app/routers/profiles.py``, ``app/routers/app_settings.py`` and
``app/routers/vision.py`` — the same code the Settings pages run.
Every result passes through :func:`app.agent_mcp.redact.redact`:
passwords and API keys are write-only, and a profile's ``env`` comes
back with its key values masked (``ANTHROPIC_AUTH_TOKEN``,
``ANTHROPIC_API_KEY``, …) while the model/base-URL rows stay
readable, so an agent can still tell profiles apart.

Payload parity with the web UI:

* Ziniao create defaults ``name`` to the company when the name is
  blank (``App.tsx`` ``createZiniaoAccount``) and defaults
  ``socket_port``/``client_path`` server-side; update sends only
  the fields it touched, and an empty password means "no change"
  (``updateZiniaoAccount``);
* profile create/update posts ``{id?, name, description, env,
  load_global_mcp}`` (``ProfileModal`` → ``App.tsx``); the UI's
  set-as-default checkbox (default ON after create) is an explicit
  ``set_default`` argument here, default off — an agent should not
  silently re-point a user's default model;
* settings update posts the same single-key bodies the UI posts;
  the tool takes one ``settings`` object and re-reads the values
  that landed, because the route normalizes (bool → 'true'/'false')
  and clamps (int ranges) before storing.
"""

from fastapi import HTTPException

from app.agent_mcp.actions import (
    action_arg,
    action_schema,
    opt_int,
    opt_str,
)
from app.agent_mcp.redact import redact, restore_masked
from app.agent_mcp.registry import tool
from app.agent_mcp.support import (
    McpContext,
    ToolError,
    call_route,
    require,
)
from app.models.ziniao_account import ZiniaoAccount
from app.routers import (
    app_settings as settings_route,
    profiles as profiles_route,
    vision as vision_route,
    ziniao_accounts as ziniao_route,
)
from app.schemas.ziniao_account import (
    ZiniaoAccountCreate,
    ZiniaoAccountResponse,
    ZiniaoAccountUpdate,
)
from app.utils.crypto import decrypt_password

# Ziniao update fields the UI can touch. Constructed with only the
# keys the caller sent so the route's exclude_unset semantics see
# "absent" for the rest.
_ZINIAO_EDIT_FIELDS = (
    'name',
    'company',
    'username',
    'password',
    'socket_port',
    'client_path',
)

# Profile create/update body fields (the POST/PUT routes take a
# plain dict; these are the keys they read).
_PROFILE_BODY_FIELDS = ('name', 'env', 'description', 'load_global_mcp')

_ZINIAO_ACTIONS = (
    'list',
    'create',
    'update',
    'delete',
    'browsers',
    'restart',
)


def _ziniao_row(account) -> dict:
    """One Ziniao account as the web UI sees it.

    The list/create/update routes return the ORM row (the HTTP
    layer's ``response_model`` is what strips ``encrypted_password``
    for the UI — a direct call bypasses that), so the adapter
    validates through the same response model before anything is
    returned. The redaction layer is the second line, not the
    first: the response model is the shape the UI trusts.
    """
    return ZiniaoAccountResponse.model_validate(account).model_dump()


async def _stored_ziniao_password(ctx: McpContext, account_id: str):
    """The stored Ziniao password, or None.

    Read from the row the update route itself will read (the HTTP
    GET never returns it, so the row is the only same-code source);
    ``restore_masked`` needs it to keep a masked echo from
    overwriting the credential.
    """
    account = await ctx.db.get(ZiniaoAccount, account_id)
    if not account or not account.encrypted_password:
        return None
    return decrypt_password(account.encrypted_password)


@tool(
    'manage_ziniao_account',
    'Manage Ziniao anti-detect browser accounts (Settings → Stores '
    '→ Ziniao). Actions: list, create, update, delete, browsers '
    '(the browser profiles on the account, needed as '
    'browser_oauth when creating a ziniao store), restart '
    '(kill-and-relaunch the Ziniao client in WebDriver mode — only '
    'allowed while it sits in normal mode, so it never kills an '
    'active WebDriver session). Passwords are write-only: they are '
    'accepted on create/update and never returned.',
    action_schema(
        {
            'account_id': {
                'type': ['string', 'null'],
                'description': (
                    'Account id (required for update/delete/browsers/restart).'
                ),
            },
            'name': {
                'type': ['string', 'null'],
                'description': (
                    'Display name; defaults to the company when blank '
                    '(as the UI does).'
                ),
            },
            'company': {
                'type': ['string', 'null'],
                'description': 'Ziniao company/tenant name.',
            },
            'username': {
                'type': ['string', 'null'],
                'description': 'Ziniao login username.',
            },
            'password': {
                'type': ['string', 'null'],
                'description': (
                    'Ziniao login password. Write-only. On update, '
                    'omit or empty means "leave unchanged".'
                ),
            },
            'socket_port': {
                'type': ['integer', 'null'],
                'description': (
                    'Local Ziniao client API port (default 16851).'
                ),
            },
            'client_path': {
                'type': ['string', 'null'],
                'description': (
                    "Path/command of the Ziniao client (default 'ziniao')."
                ),
            },
        },
        actions=_ZINIAO_ACTIONS,
    ),
)
async def manage_ziniao_account(ctx: McpContext, args: dict) -> dict:
    action = action_arg(args, _ZINIAO_ACTIONS)

    if action == 'list':
        accounts = await call_route(
            ziniao_route.list_accounts, db=ctx.db, _user=ctx.user
        )
        return redact({'accounts': [_ziniao_row(a) for a in accounts]})

    if action == 'create':
        company = require(args, 'company')
        data = ZiniaoAccountCreate(
            name=opt_str(args, 'name') or company,
            company=company,
            username=require(args, 'username'),
            # A mask here can only be an echo of something that does
            # not exist yet — restore_masked says so rather than
            # storing '****…' as the credential.
            password=restore_masked(
                {'password': require(args, 'password')},
                {'password': None},
            )['password'],
            socket_port=opt_int(args, 'socket_port') or 16851,
            client_path=opt_str(args, 'client_path') or 'ziniao',
        )
        account = await call_route(
            ziniao_route.create_account, data, db=ctx.db, _user=ctx.user
        )
        return redact({'created': _ziniao_row(account)})

    if action == 'update':
        account_id = require(args, 'account_id')
        fields = {
            key: args[key]
            for key in _ZINIAO_EDIT_FIELDS
            if args.get(key) is not None
        }
        if not fields:
            raise ToolError(
                'update needs at least one of: '
                + ', '.join(_ZINIAO_EDIT_FIELDS)
            )
        if 'password' in fields:
            fields['password'] = restore_masked(
                {'password': fields['password']},
                {'password': await _stored_ziniao_password(ctx, account_id)},
            )['password']
        account = await call_route(
            ziniao_route.update_account,
            account_id,
            ZiniaoAccountUpdate(**fields),
            db=ctx.db,
            _user=ctx.user,
        )
        return redact({'updated': _ziniao_row(account)})

    if action == 'delete':
        account_id = require(args, 'account_id')
        result = await call_route(
            ziniao_route.delete_account,
            account_id,
            db=ctx.db,
            _user=ctx.user,
        )
        return redact(result or {'ok': True})

    if action == 'browsers':
        account_id = require(args, 'account_id')
        browsers = await call_route(
            ziniao_route.list_browsers,
            account_id,
            db=ctx.db,
            _user=ctx.user,
        )
        return redact({'browsers': [b.model_dump() for b in browsers]})

    # restart
    account_id = require(args, 'account_id')
    result = await call_route(
        ziniao_route.restart_ziniao,
        account_id,
        db=ctx.db,
        _user=ctx.user,
    )
    return redact(result or {'ok': True})


_PROFILE_ACTIONS = (
    'list',
    'get',
    'create',
    'update',
    'delete',
    'set_default',
    'validate',
    'presets',
)


async def _stored_profile_env(ctx: McpContext, profile_id: str) -> dict:
    """The env a profile stores, read through the same list route the
    UI reads (never a direct DB/file access). Empty when the profile
    does not exist — the update route will say so itself."""
    result = await call_route(profiles_route.list_profiles, _user=ctx.user)
    for profile in result.get('profiles', []):
        if profile.get('id') == profile_id:
            return profile.get('env') or {}
    return {}


def _merged_env(incoming: dict, stored: dict) -> dict:
    """The env to persist for an update: the stored env, overlaid
    with the incoming keys, minus the keys the caller set to null.

    The REST route replaces ``env`` wholesale (a top-level
    ``dict.update``), which is right for the UI — its edit form
    always posts the full env it loaded. An MCP agent edits ONE key
    of the env it read (masked), so the tool computes the merge the
    UI's form implies and sends the result. Null deletes a key;
    ``restore_masked`` has already put the real secret back under
    any masked value by the time this runs.
    """
    merged = dict(stored)
    for key, value in incoming.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


@tool(
    'manage_profile',
    'Manage AI profiles (Settings → AI) — which model/endpoint a '
    'task runs under. Actions: list, get, create, update, delete, '
    'set_default (make it the account default), validate (probe an '
    'env WITHOUT saving — NOTE: it spends one tiny real model '
    'call), presets (provider templates that pre-fill env). API '
    'keys inside env are write-only: accepted, never returned '
    '(masked). Deleting the default profile resets the default to '
    "'default'.",
    action_schema(
        {
            'profile_id': {
                'type': ['string', 'null'],
                'description': (
                    'Profile id (required for get/update/delete/'
                    'set_default; the new id for create).'
                ),
            },
            'name': {
                'type': ['string', 'null'],
                'description': 'Display name (required for create).',
            },
            'env': {
                'type': 'object',
                'description': (
                    'Env vars, e.g. {"ANTHROPIC_BASE_URL": …, '
                    '"ANTHROPIC_MODEL": …, "ANTHROPIC_AUTH_TOKEN": …}. '
                    'Use presets first and override. On update the env '
                    'MERGES with the stored one: keys you omit stay '
                    'unchanged, a null value DELETES a key, and an '
                    'API-key value you only have as a mask (****…) '
                    'keeps the stored key.'
                ),
                'additionalProperties': {'type': ['string', 'null']},
            },
            'description': {
                'type': ['string', 'null'],
                'description': 'Free-form note.',
            },
            'load_global_mcp': {
                'type': ['boolean', 'null'],
                'description': (
                    'Load the machine-wide MCP servers into runs of '
                    'this profile (default false).'
                ),
            },
            'set_default': {
                'type': ['boolean', 'null'],
                'description': (
                    'create only: also make the new profile the '
                    'account default. The UI checkbox defaults on; '
                    'here it is opt-in, because it silently re-points '
                    'every future task.'
                ),
            },
        },
        actions=_PROFILE_ACTIONS,
    ),
)
async def manage_profile(ctx: McpContext, args: dict) -> dict:
    action = action_arg(args, _PROFILE_ACTIONS)

    if action == 'list':
        result = await call_route(profiles_route.list_profiles, _user=ctx.user)
        return redact(result)

    if action == 'get':
        profile_id = require(args, 'profile_id')
        result = await call_route(profiles_route.list_profiles, _user=ctx.user)
        for profile in result.get('profiles', []):
            if profile.get('id') == profile_id:
                return redact({'profile': profile})
        raise ToolError(f'Profile not found: {profile_id}')

    if action == 'create':
        profile_id = require(args, 'profile_id')
        name = require(args, 'name')
        body = {
            'id': profile_id,
            'name': name,
            'env': restore_masked(args.get('env') or {}, {}),
            'description': args.get('description') or '',
            'load_global_mcp': bool(args.get('load_global_mcp')),
        }
        profile = await call_route(
            profiles_route.create_profile, body, _user=ctx.user
        )
        out = redact({'created': profile})
        if args.get('set_default'):
            # Best-effort, like the UI: the profile exists already,
            # so a set-default hiccup (e.g. the external-config 409)
            # is reported, not raised over the successful create.
            try:
                out['default'] = await call_route(
                    profiles_route.set_default_profile,
                    profile_id,
                    db=ctx.db,
                    current_user=ctx.user,
                )
            except HTTPException as exc:
                out['default_error'] = str(exc.detail)
        return out

    if action == 'update':
        profile_id = require(args, 'profile_id')
        stored_env = await _stored_profile_env(ctx, profile_id)
        fields = {}
        for key in _PROFILE_BODY_FIELDS:
            if args.get(key) is not None:
                fields[key] = args[key]
        if not fields:
            raise ToolError(
                'update needs at least one of: '
                + ', '.join(_PROFILE_BODY_FIELDS)
            )
        if 'env' in fields:
            fields['env'] = _merged_env(
                restore_masked(fields['env'], stored_env), stored_env
            )
        profile = await call_route(
            profiles_route.update_profile,
            profile_id,
            fields,
            _user=ctx.user,
        )
        return redact({'updated': profile})

    if action == 'delete':
        profile_id = require(args, 'profile_id')
        result = await call_route(
            profiles_route.delete_profile,
            profile_id,
            current_user=ctx.user,
            db=ctx.db,
        )
        return redact(result or {'ok': True})

    if action == 'set_default':
        profile_id = require(args, 'profile_id')
        result = await call_route(
            profiles_route.set_default_profile,
            profile_id,
            db=ctx.db,
            current_user=ctx.user,
        )
        return redact(result or {'ok': True})

    if action == 'validate':
        # The UI runs this before saving; it makes one tiny real
        # model call against the given env. Never persisted — but a
        # masked API key echoed from a read must probe the STORED
        # key, or the verdict is about '****1234', not the profile.
        profile_id = opt_str(args, 'profile_id')
        stored = (
            await _stored_profile_env(ctx, profile_id) if profile_id else {}
        )
        result = await call_route(
            profiles_route.validate_profile,
            {'env': restore_masked(args.get('env') or {}, stored)},
            _user=ctx.user,
        )
        return redact(result)

    # presets
    result = await call_route(
        profiles_route.get_provider_presets, _user=ctx.user
    )
    return redact(result)


_SETTINGS_ACTIONS = (
    'get',
    'update',
    'gws_status',
    'gws_enable',
    'gws_disable',
    'vision_get',
    'vision_update',
)


@tool(
    'app_settings',
    'Server settings, the Google Workspace integration and the '
    'vision (image-generation) key — the Settings → General / '
    'Integrations / AI → Vision pages over MCP. Actions: get, '
    'update (admin only; the update re-reads every value you sent '
    'so you see what actually landed — the route normalizes and '
    'clamps), gws_status, gws_enable, gws_disable (Google '
    'Workspace; enable/disable are admin only and run the '
    'install/uninstall pipeline), vision_get, vision_update '
    '(admin only; the kie.ai key is write-only and comes back only '
    'masked).',
    action_schema(
        {
            'settings': {
                'type': ['object', 'null'],
                'description': (
                    'update: the keys to set, as the UI sends them — '
                    'auth_required, browser_headless, '
                    'max_agent_concurrency, default_schedule_phase_mode, '
                    'default_schedule_timezone, telemetry_enabled, '
                    'skills_auto_sync_enabled, task_retention_days.'
                ),
                'additionalProperties': True,
            },
            'kie_api_key': {
                'type': ['string', 'null'],
                'description': (
                    'vision_update: the kie.ai API key. Write-only; '
                    'the result only reports whether one is set.'
                ),
            },
        },
        actions=_SETTINGS_ACTIONS,
    ),
)
async def app_settings(ctx: McpContext, args: dict) -> dict:
    action = action_arg(args, _SETTINGS_ACTIONS)

    if action == 'get':
        result = await call_route(settings_route.get_settings, db=ctx.db)
        return redact(result)

    if action == 'update':
        body = args.get('settings')
        if not isinstance(body, dict) or not body:
            raise ToolError(
                'settings is required for update: an object of '
                'setting key -> value'
            )
        # The ROUTE silently drops unknown keys; an agent deserves a
        # name, not a silent no-op. The accepted set is the route's
        # own constant, so the two cannot drift.
        unknown = sorted(
            key for key in body if key not in settings_route.ALLOWED_KEYS
        )
        if unknown:
            raise ToolError(
                f'Unknown setting(s): {", ".join(unknown)}. Accepted: '
                + ', '.join(sorted(settings_route.ALLOWED_KEYS))
            )
        result = await call_route(
            settings_route.update_settings,
            body,
            db=ctx.db,
            current_user=ctx.user,
        )
        # Report what landed: the route clamps ints and normalizes
        # bools, so echoing the input would lie.
        after = await call_route(settings_route.get_settings, db=ctx.db)
        return redact({
            **(result or {'ok': True}),
            'values': {key: after.get(key) for key in body},
        })

    if action == 'gws_status':
        result = await call_route(
            settings_route.gws_status, db=ctx.db, current_user=ctx.user
        )
        return redact(result)

    if action == 'gws_enable':
        result = await call_route(
            settings_route.gws_enable, db=ctx.db, current_user=ctx.user
        )
        return redact(result)

    if action == 'gws_disable':
        result = await call_route(
            settings_route.gws_disable, db=ctx.db, current_user=ctx.user
        )
        return redact(result)

    if action == 'vision_get':
        result = await call_route(
            vision_route.get_vision_config, current_user=ctx.user
        )
        return redact(result)

    # vision_update — a masked key can only be an echo of the one
    # already stored (the GET reports only a mask); restore it to
    # keep the stored key rather than overwriting it with '****'.
    key = restore_masked(
        {'kie_api_key': require(args, 'kie_api_key')},
        {'kie_api_key': vision_route.vision.get_kie_api_key()},
    )['kie_api_key']
    result = await call_route(
        vision_route.put_vision_config,
        {'kie_api_key': key},
        current_user=ctx.user,
    )
    return redact(result)
