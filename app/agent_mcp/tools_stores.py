"""The store and browser tools: ``manage_store`` and ``browser``.

Both are thin adapters over the REST route handlers in
``app/routers/stores.py`` and ``app/routers/browser.py`` — the same
code the Settings → Stores page and the browser wrappers run — and
every result passes through :func:`app.agent_mcp.redact.redact`
before it leaves the tool.

Payload parity with the web UI (read from
``frontend/src/App.tsx`` ``createStore`` and
``components/settings/StoresPanel.tsx`` ``updateStore``):

* create sends ``name``, ``browser_backend``, and — only when
  relevant — ``browser_config`` (chrome proxy), ``ziniao_account_id``
  and ``browser_oauth`` (ziniao). Platforms/countries default
  server-side (``['amazon']`` / ``['US']``), exactly as they do for
  the UI, which does not send them either;
* update sends only the fields the ``StoreUpdate`` schema honours
  (the UI also posts ``browser_backend``, which the route ignores —
  the tool does not offer it, so no argument is silently dropped);
* delete mirrors the UI's ``?delete_files=`` checkbox.
"""

from app.agent_mcp.actions import action_arg, action_schema, opt_str
from app.agent_mcp.redact import redact
from app.agent_mcp.registry import tool
from app.agent_mcp.support import (
    McpContext,
    ToolError,
    call_route,
    require,
)
from app.routers import browser as browser_route, stores as stores_route
from app.schemas.store import StoreCreate, StoreUpdate

_ACTIONS = ('list', 'get', 'create', 'update', 'delete', 'bookmarks')


@tool(
    'manage_store',
    "Manage the server's stores — the Settings → Stores page over "
    'MCP. Actions: list, get, create, update, delete, bookmarks '
    "(a store's browser bookmarks). Deleting a store deletes its "
    'tasks and schedules (the route refuses while a task on it is '
    'active); pass delete_files=true to also remove its workspace '
    'and browser profile — ask your user before deleting anything.',
    action_schema(
        {
            'store_id': {
                'type': ['string', 'null'],
                'description': 'Store id (required for get/update/'
                'delete/bookmarks).',
            },
            'name': {
                'type': ['string', 'null'],
                'description': 'Store name (required for create).',
            },
            'browser_backend': {
                'type': ['string', 'null'],
                'enum': ['chrome', 'ziniao', 'winchrome'],
                'description': (
                    "Browser backend for create: 'chrome' (default) "
                    "or 'ziniao' (anti-detect client)."
                ),
            },
            'browser_config': {
                'type': 'object',
                'description': (
                    'Backend config, as the UI sends it: '
                    "{'proxy_server': …, 'proxy_bypass': …} for a "
                    'chrome store. Omit for none.'
                ),
                'additionalProperties': {'type': 'string'},
            },
            'ziniao_account_id': {
                'type': ['string', 'null'],
                'description': (
                    'Ziniao account id — required for a ziniao store '
                    '(the UI refuses to create one without it).'
                ),
            },
            'browser_oauth': {
                'type': ['string', 'null'],
                'description': (
                    'Ziniao browser profile oauth, from '
                    'manage_ziniao_account(action=browsers) — '
                    'required for a ziniao store.'
                ),
            },
            'platforms': {
                'type': ['array', 'null'],
                'items': {'type': 'string'},
                'description': (
                    "Marketplaces (create/update). Default ['amazon']."
                ),
            },
            'countries': {
                'type': ['array', 'null'],
                'items': {'type': 'string'},
                'description': "Countries (create/update). Default ['US'].",
            },
            'platform_countries': {
                'type': ['object', 'null'],
                'description': (
                    'Per-marketplace countries, e.g. '
                    "{'amazon': ['US', 'AE']} (create/update)."
                ),
                'additionalProperties': {
                    'type': 'array',
                    'items': {'type': 'string'},
                },
            },
            'delete_files': {
                'type': ['boolean', 'null'],
                'description': (
                    'delete: also remove the store workspace and '
                    'browser profile (default false, as the UI '
                    'checkbox defaults).'
                ),
            },
        },
        actions=_ACTIONS,
    ),
)
async def manage_store(ctx: McpContext, args: dict) -> dict:
    action = action_arg(args, _ACTIONS)

    if action == 'list':
        stores = await call_route(
            stores_route.list_stores, db=ctx.db, _user=ctx.user
        )
        return redact({'stores': stores})

    if action == 'get':
        store_id = require(args, 'store_id')
        store = await call_route(
            stores_route.get_store, store_id, db=ctx.db, _user=ctx.user
        )
        return redact({'store': store})

    if action == 'create':
        name = require(args, 'name')
        backend = opt_str(args, 'browser_backend') or 'chrome'
        data = StoreCreate(
            name=name,
            browser_backend=backend,
            browser_config=args.get('browser_config') or {},
            ziniao_account_id=opt_str(args, 'ziniao_account_id'),
            browser_oauth=opt_str(args, 'browser_oauth'),
            platforms=args.get('platforms') or ['amazon'],
            countries=args.get('countries') or ['US'],
            platform_countries=args.get('platform_countries') or {},
        )
        store = await call_route(
            stores_route.create_store, data, db=ctx.db, _user=ctx.user
        )
        return redact({'created': store})

    if action == 'update':
        store_id = require(args, 'store_id')
        fields = {}
        for key in (
            'name',
            'browser_config',
            'platforms',
            'countries',
            'platform_countries',
        ):
            if args.get(key) is not None:
                fields[key] = args[key]
        if not fields:
            raise ToolError(
                'update needs at least one of: name, browser_config, '
                'platforms, countries, platform_countries'
            )
        store = await call_route(
            stores_route.update_store,
            store_id,
            StoreUpdate(**fields),
            db=ctx.db,
            _user=ctx.user,
        )
        return redact({'updated': store})

    if action == 'delete':
        store_id = require(args, 'store_id')
        result = await call_route(
            stores_route.delete_store,
            store_id,
            delete_files=bool(args.get('delete_files') or False),
            db=ctx.db,
            _user=ctx.user,
        )
        return redact(result or {'ok': True})

    # bookmarks
    store_id = require(args, 'store_id')
    bookmarks = await call_route(
        stores_route.get_store_bookmarks, store_id, db=ctx.db, _user=ctx.user
    )
    return redact({'bookmarks': bookmarks})


_BROWSER_ACTIONS = (
    'start_store',
    'start_aux',
    'start_web',
    'ziniao_launcher',
)


@tool(
    'browser',
    "Start a browser session: a store's main browser "
    "(start_store), a store's login-less aux browser "
    '(start_aux), or the store-less web browser (start_web). These '
    'are the SAME routes the store wrappers call lazily — a start '
    'REUSES a healthy running session and never kills a running '
    "task's browser (a full relaunch only happens when the session's "
    "CDP proxy is dead, under the server's relaunch budget). "
    'ziniao_launcher returns the download URL of the Windows '
    'WebDriver launcher script — the URL is for a HUMAN to open on '
    'the Windows machine, not for you.',
    action_schema(
        {
            'store_id': {
                'type': ['string', 'null'],
                'description': (
                    'The store (required for start_store / start_aux).'
                ),
            },
            'force': {
                'type': ['boolean', 'null'],
                'description': (
                    'start_store only: when Ziniao sits in normal '
                    '(non-WebDriver) mode, true kills and relaunches '
                    'the Ziniao client in WebDriver mode, then retries '
                    "— the UI's Force Restart. Ask your user first; "
                    'default false keeps the safe error instead.'
                ),
            },
        },
        actions=_BROWSER_ACTIONS,
    ),
)
async def browser(ctx: McpContext, args: dict) -> dict:
    action = action_arg(args, _BROWSER_ACTIONS)

    if action == 'start_store':
        store_id = require(args, 'store_id')
        result = await call_route(
            stores_route.start_browser,
            store_id,
            force=bool(args.get('force') or False),
            db=ctx.db,
            _user=ctx.user,
        )
        return redact(result or {'ok': True})

    if action == 'start_aux':
        store_id = require(args, 'store_id')
        result = await call_route(
            browser_route.start_aux_browser,
            store_id,
            db=ctx.db,
            _user=ctx.user,
        )
        return redact(result or {'ok': True})

    if action == 'start_web':
        result = await call_route(
            browser_route.start_web_browser,
            force=False,
            db=ctx.db,
            _user=ctx.user,
        )
        return redact(result or {'ok': True})

    # ziniao_launcher — run the route (it 404s when the script is
    # missing from the install) but return a download URL, not the
    # file: a .bat for a human on the Windows machine. An agent has
    # no use for the bytes and cannot execute them.
    await call_route(browser_route.download_ziniao_launcher)
    base = str(ctx.request.base_url)
    return {
        'ok': True,
        'download_url': f'{base}api/ziniao/launcher',
        'note': (
            'The launcher is a Windows .bat. Your user must download '
            'and run it on the Windows machine where the Ziniao '
            'client lives (WSL setups). You cannot run it.'
        ),
    }
