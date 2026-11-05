"""The ``/api/mcp`` endpoint — JSON-RPC dispatch over the toolset.

One JSON document per call, no sessions, no event stream:

    POST /api/mcp
    {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
     "params": {"name": "list_tasks", "arguments": {}}}

Auth is the REST dependency (``Depends(get_current_user)``), so a
cookie, a ``vst_`` bearer token and a no-auth server all behave
exactly as they do for the web UI — including the 401 that carries a
``WWW-Authenticate: Bearer`` header.

Before the body is parsed, the transport guard (below) enforces the
Content-Type and cross-site rules.
"""

import json
import logging
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_mcp.lifecycle import render_instructions
from app.agent_mcp.protocol import (
    FORBIDDEN,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    UNAUTHORIZED,
    failure,
    is_notification,
    negotiate_version,
    success,
)
from app.agent_mcp.tools import (
    McpContext,
    ToolError,
    find_tool,
    tool_definitions,
)
from app.auth import get_current_user
from app.config import CORS_ORIGINS
from app.database import get_db
from app.models.user import User
from app.version import get_version

logger = logging.getLogger(__name__)

SERVER_NAME = 'vibe-seller'

# The instructions a client may show the model at connect time. The
# lifecycle part is RENDERED from the table in
# ``app/agent_mcp/lifecycle.py`` — the same table every tool result
# carries — so the vocabulary the model starts with cannot drift from
# the meanings it is given later. The short part on top is the
# calling guidance: which tool for which job. Kept under ~400
# tokens together (the lifecycle rendering is ~285).
_CALLING_GUIDANCE = (
    'create_task starts work; wait_task waits for the next thing that '
    'needs you (call it again after a timeout); get_task reads the '
    'detail; get_pending_questions plus answer_question answer what '
    'the agent asked; review_plan approves or revises a plan — show '
    'it to your user and approve only with their go-ahead. Never '
    'report a task result the agent did not produce.'
)

INSTRUCTIONS = f'{render_instructions()}\n\n{_CALLING_GUIDANCE}'


class _MCPRoute(APIRoute):
    """Route that answers a 401 with ``WWW-Authenticate: Bearer``.

    The MCP spec asks for the header so a client can tell "you are
    not authenticated" from "the server is broken". FastAPI's default
    401 carries no such header, and adding it to every 401 in the app
    would change unrelated routes — so it is added here, for this
    route only.

    The dependency raises ``HTTPException`` before the endpoint runs,
    so the exception is caught around the route handler and answered
    with the MCP error envelope plus the challenge header.
    """

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except HTTPException as exc:
                if exc.status_code != 401:
                    raise
                return JSONResponse(
                    failure(None, UNAUTHORIZED, str(exc.detail)),
                    status_code=401,
                    headers={'WWW-Authenticate': 'Bearer'},
                )

        return handler


router = APIRouter(prefix='/api/mcp', tags=['mcp'], route_class=_MCPRoute)


def _is_json_media_type(media_type: str) -> bool:
    """Whether *media_type* carries a JSON document.

    ``application/json`` itself, or any structured suffix type
    (``application/…+json``). Parameters (``; charset=utf-8``) are
    stripped by the caller.
    """
    return media_type == 'application/json' or media_type.endswith('+json')


def _transport_guard(request: Request) -> JSONResponse | None:
    """Refuse the request before the body is parsed — or None to pass.

    WHY THIS EXISTS. This endpoint parses whatever body it is given,
    which made it the app's first write surface a BROWSER can drive
    cross-site without a preflight: a "simple request" is one whose
    Content-Type is not JSON, and every REST write route is already
    safe against those precisely because FastAPI rejects a non-JSON
    Content-Type at 422. An attacker's page could POST
    ``text/plain`` JSON-RPC at an auth-off server (the product
    default) and create tasks as the default admin. The page cannot
    read the response — but every write lands.

    Three checks, cheapest first, all before the body is read and
    long before any tool runs:

    1. **Content-Type must be JSON** (``application/json`` or any
       ``+json`` suffix type; parameters allowed), else **415**. This
       alone re-enables the preflight shield: a cross-site
       ``Content-Type: application/json`` fetch is not a simple
       request, the browser preflights it, and the preflight fails.
       Every real client (Claude Code ``--transport http``, Codex,
       WorkBuddy, the reference SDK, curl) already sends it.
    2. **``Sec-Fetch-Site: cross-site`` → 403.** When the browser
       volunteers that the request is cross-site, believe it —
       belt-and-braces for clients/CDNs that pass preflight-less
       JSON (e.g. through a proxy that rewrites the header).
    3. **``Origin``, when present, must be same-origin** (its
       host:port equals the request's ``Host`` header) **or in the
       app's CORS allow-list** (``app.config.CORS_ORIGINS`` — the
       same list CORSMiddleware uses, so the two cannot disagree),
       else **403**. Non-browser clients send no ``Origin`` and are
       unaffected.
    """
    content_type = request.headers.get('content-type', '')
    media_type = content_type.split(';', 1)[0].strip().lower()
    if not _is_json_media_type(media_type):
        return JSONResponse(
            failure(
                None,
                INVALID_REQUEST,
                'Content-Type must be application/json '
                f'(got {media_type or "none"})',
            ),
            status_code=415,
        )
    if request.headers.get('sec-fetch-site', '').strip().lower() == (
        'cross-site'
    ):
        return JSONResponse(
            failure(
                None,
                FORBIDDEN,
                'Cross-site requests are not allowed on this endpoint',
            ),
            status_code=403,
        )
    origin = request.headers.get('origin', '').strip()
    if origin:
        same_origin = urlsplit(origin).netloc == request.headers.get('host', '')
        if not same_origin and origin not in CORS_ORIGINS:
            return JSONResponse(
                failure(
                    None,
                    FORBIDDEN,
                    f'Origin {origin} is not allowed on this endpoint',
                ),
                status_code=403,
            )
    return None


def _initialize_result(params: dict) -> dict:
    """The ``initialize`` result."""
    requested = (
        params.get('protocolVersion') if isinstance(params, dict) else None
    )
    return {
        'protocolVersion': negotiate_version(requested),
        'capabilities': {'tools': {'listChanged': False}},
        'serverInfo': {'name': SERVER_NAME, 'version': get_version()},
        'instructions': INSTRUCTIONS,
    }


def _tool_error(request_id, message: str) -> dict:
    """A tool that ran and failed: ``isError`` with the reason."""
    return success(
        request_id,
        {'content': [{'type': 'text', 'text': message}], 'isError': True},
    )


def _argument_problem(definition, arguments: dict) -> str | None:
    """Why *arguments* do not fit the tool's schema, if they do not.

    The advertised schema says ``additionalProperties: false`` — so the
    server had better mean it. A silently ignored argument is the
    worst failure mode for an agent: it believes it filtered by store
    and gets every task instead. Saying "unknown argument" costs one
    round trip and lets it fix the call.
    """
    schema = definition.input_schema
    properties = schema.get('properties', {})
    unknown = sorted(key for key in arguments if key not in properties)
    if unknown:
        accepted = ', '.join(sorted(properties)) or '(none)'
        return (
            f'Unknown argument(s) for {definition.name}: '
            f'{", ".join(unknown)}. Accepted: {accepted}'
        )
    missing = [
        key
        for key in schema.get('required', [])
        if arguments.get(key) in (None, '')
    ]
    if missing:
        return (
            f'Missing required argument(s) for {definition.name}: '
            f'{", ".join(missing)}'
        )
    return None


async def _call_tool(message: dict, ctx: McpContext) -> dict:
    """Run one tool and wrap its result in the MCP envelope."""
    request_id = message.get('id')
    params = message.get('params') or {}
    name = params.get('name')
    if not isinstance(name, str) or not name:
        return failure(
            request_id, INVALID_PARAMS, 'tools/call requires a tool name'
        )
    definition = find_tool(name)
    if definition is None:
        return failure(request_id, METHOD_NOT_FOUND, f'Unknown tool: {name}')
    arguments = params.get('arguments') or {}
    if not isinstance(arguments, dict):
        return failure(
            request_id, INVALID_PARAMS, 'arguments must be an object'
        )
    problem = _argument_problem(definition, arguments)
    if problem:
        return failure(request_id, INVALID_PARAMS, problem)
    try:
        result = await definition.handler(ctx, arguments)
    except ToolError as exc:
        return _tool_error(request_id, str(exc))
    except HTTPException as exc:
        return _tool_error(request_id, str(exc.detail))
    except Exception:
        logger.exception('MCP tool %r failed', name)
        return _tool_error(
            request_id, f'Tool {name} failed unexpectedly — see server logs'
        )
    return success(
        request_id,
        {
            'content': [
                {
                    'type': 'text',
                    'text': json.dumps(
                        result,
                        ensure_ascii=False,
                        indent=2,
                        default=str,
                    ),
                }
            ],
            'isError': False,
        },
    )


async def _dispatch(message: dict, ctx: McpContext) -> dict | None:
    """Handle one JSON-RPC message. None means "no response"."""
    method = message.get('method')
    if not isinstance(method, str) or not method:
        return failure(message.get('id'), INVALID_REQUEST, 'method is required')
    # ``params`` must be an object when present. A truthy list or
    # string would otherwise crash on ``.get`` below — before any tool
    # try/except — and surface as a raw 500. Probe-grade input, but a
    # JSON-RPC server answers -32602, not a stack trace.
    params = message.get('params')
    if params is not None and not isinstance(params, dict):
        return failure(
            message.get('id'), INVALID_PARAMS, 'params must be an object'
        )
    # A notification carries no id and gets no answer — the handshake
    # ends with `notifications/initialized`.
    if is_notification(message):
        return None
    if method == 'initialize':
        return success(
            message.get('id'), _initialize_result(message.get('params') or {})
        )
    if method == 'ping':
        return success(message.get('id'), {})
    if method == 'tools/list':
        return success(message.get('id'), {'tools': tool_definitions()})
    if method == 'tools/call':
        return await _call_tool(message, ctx)
    return failure(
        message.get('id'), METHOD_NOT_FOUND, f'Method not found: {method}'
    )


@router.post('')
async def mcp_endpoint(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """The MCP endpoint. One JSON-RPC message (or a batch) per call."""
    guard = _transport_guard(request)
    if guard is not None:
        return guard

    raw = await request.body()
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(
            failure(None, PARSE_ERROR, 'Invalid JSON body'), status_code=400
        )

    ctx = McpContext(user=user, db=db, request=request)

    if isinstance(payload, list):
        if not payload:
            return JSONResponse(
                failure(None, INVALID_REQUEST, 'Empty batch'),
                status_code=400,
            )
        responses = []
        for message in payload:
            if not isinstance(message, dict):
                responses.append(
                    failure(
                        None,
                        INVALID_REQUEST,
                        'Batch entries must be JSON objects',
                    )
                )
                continue
            response = await _dispatch(message, ctx)
            if response is not None:
                responses.append(response)
        if not responses:
            return Response(status_code=202)
        return JSONResponse(responses)

    if not isinstance(payload, dict):
        return JSONResponse(
            failure(
                None,
                INVALID_REQUEST,
                'Request must be a JSON object or a batch of them',
            ),
            status_code=400,
        )

    response = await _dispatch(payload, ctx)
    if response is None:
        return Response(status_code=202)
    return JSONResponse(response)


@router.get('')
async def mcp_no_event_stream() -> Response:
    """This endpoint is stateless — there is no server-to-client stream.

    A client that opens the optional GET stream gets 405, which the
    MCP clients read as "this server does not push events".
    """
    return JSONResponse(
        {
            'error': (
                'This MCP endpoint is stateless: it answers one JSON '
                'document per POST and does not offer an event stream.'
            )
        },
        status_code=405,
        headers={'Allow': 'POST'},
    )


__all__ = ['router']
