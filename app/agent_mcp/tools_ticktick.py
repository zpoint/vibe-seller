"""TickTick / Dida365 integration tool (Settings → Integrations).

Split out of ``tools_comm.py``; same rules: every action calls the
REST route through ``call_route`` and results pass through
``redact`` (the OAuth client secret is write-only).

``manage_ticktick(authorize=…)`` returns a URL only a HUMAN can finish —
the OAuth redirect lands in their browser, not in the agent's.
"""

from app.agent_mcp.actions import (
    action_arg,
    action_schema,
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
from app.routers import (
    dida365_oauth as dida_route,
)
from app.routers.dida365_oauth import AuthorizeRequest, ConfigureRequest
from app.utils.crypto import decrypt_password

_TICKTICK_ACTIONS = (
    'status',
    'setup_mcp',
    'authorize',
    'projects',
    'configure',
    'disconnect',
)


@tool(
    'manage_ticktick',
    'The TickTick / Dida365 integration (Settings → Integrations): '
    'tasks land in the connected project. Actions: status, '
    'setup_mcp (clone+install the ticktick-mcp helper — network, '
    'can take a minute; run before the first authorize), authorize '
    '(start OAuth: returns the auth_url for a HUMAN to open and '
    'approve — YOU cannot finish OAuth, the redirect lands in '
    'their browser; then poll status until connected), projects '
    '(list projects to pick from), configure (set the project id), '
    'disconnect. The client secret is write-only.',
    action_schema(
        {
            'client_id': {
                'type': ['string', 'null'],
                'description': 'authorize: the OAuth app client id.',
            },
            'client_secret': {
                'type': ['string', 'null'],
                'description': (
                    'authorize: the OAuth app client secret. Write-only.'
                ),
            },
            'service_type': {
                'type': ['string', 'null'],
                'enum': ['dida365', 'ticktick', None],
                'description': (
                    "authorize: 'dida365' (default, China) or "
                    "'ticktick' (international)."
                ),
            },
            'project_id': {
                'type': ['string', 'null'],
                'description': (
                    'configure: the project id (from projects); empty '
                    'string clears the choice.'
                ),
            },
        },
        actions=_TICKTICK_ACTIONS,
    ),
)
async def manage_ticktick(ctx: McpContext, args: dict) -> dict:
    action = action_arg(args, _TICKTICK_ACTIONS)

    if action == 'status':
        result = await call_route(dida_route.get_status, _user=ctx.user)
        return redact(result)

    if action == 'setup_mcp':
        result = await call_route(dida_route.setup_mcp, _user=ctx.user)
        return redact(result)

    if action == 'authorize':
        # A masked client_secret is an echo of the one already stored
        # (the config keeps it encrypted); restore it so a re-auth
        # keeps the credential instead of writing '****' into it.
        stored_config = dida_route.load_backend_config('dida365')
        stored_secret = stored_config.get('client_secret_enc')
        body = AuthorizeRequest(
            client_id=require(args, 'client_id'),
            client_secret=restore_masked(
                {'client_secret': require(args, 'client_secret')},
                {
                    'client_secret': (
                        decrypt_password(stored_secret)
                        if stored_secret
                        else None
                    )
                },
            )['client_secret'],
            service_type=opt_str(args, 'service_type') or 'dida365',
        )
        result = await call_route(
            dida_route.authorize, body, request=ctx.request, _user=ctx.user
        )
        return redact({
            **result,
            'note': (
                'Give auth_url to your user to open in THEIR browser '
                'and approve. You cannot complete this step; poll '
                'manage_ticktick(action=status) until connected is '
                'true, then list projects and configure one.'
            ),
        })

    if action == 'projects':
        result = await call_route(dida_route.list_projects, _user=ctx.user)
        return redact({'projects': result})

    if action == 'configure':
        if 'project_id' not in args or not isinstance(
            args.get('project_id'), str
        ):
            raise ToolError(
                'project_id is required for configure '
                '(empty string clears the choice)'
            )
        result = await call_route(
            dida_route.configure,
            ConfigureRequest(project_id=args['project_id']),
            _user=ctx.user,
        )
        return redact(result)

    # disconnect
    result = await call_route(dida_route.disconnect, _user=ctx.user)
    return redact(result)
