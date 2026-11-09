"""The interactive-only guard: routes only the web UI's session may call.

Mirrors ``test_api_token_scope.py``:

* drift guards — any route whose request body can carry an
  interactive-only field (``allow_send``), or whose handler assigns
  one, must be in ``INTERACTIVE_ONLY_ROUTES``; every table entry is a
  real route; and the external MCP toolset never calls one of these
  handlers in-process (that would walk around the middleware);
* middleware mechanics on a scratch ASGI app — every machine
  credential is refused before the app runs; the web UI's session
  cookie and a no-credential request pass; other routes are untouched.
"""

import inspect
import json
from pathlib import Path
import re

from pydantic import BaseModel
import pytest

from app.auth import create_token
from app.config import AI_BOT_USER_ID
from app.interactive_only import (
    INTERACTIVE_ONLY_FIELDS,
    INTERACTIVE_ONLY_ROUTES,
    InteractiveOnlyMiddleware,
    matching_route,
    presents_machine_credential,
)
from app.main import app

pytestmark = pytest.mark.unit

PATH = '/api/email-accounts/acct-1/send-permission'


def _route_views(routes):
    for route in routes:
        candidates = getattr(route, 'effective_candidates', None)
        if callable(candidates):
            yield from _route_views(candidates())
            continue
        if hasattr(route, 'methods') and hasattr(route, 'dependant'):
            yield route


def _api_routes():
    yield from _route_views(app.routes)


def _norm(path: str) -> str:
    return re.sub(r'\{[^/]+\}', '{}', path)


def _covered() -> set[tuple[str, str]]:
    return {
        (method, _norm(r.template))
        for r in INTERACTIVE_ONLY_ROUTES
        for method in r.methods
    }


def _body_fields(route) -> set[str]:
    names: set[str] = set()
    for param in route.dependant.body_params:
        annotation = getattr(param.field_info, 'annotation', None)
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            names |= set(annotation.model_fields)
        else:
            names.add(param.name)
    return names


_ASSIGNS = [
    re.compile(rf'\.{field}\s*=[^=]') for field in INTERACTIVE_ONLY_FIELDS
]


class TestDriftGuard:
    def test_every_route_accepting_the_field_is_interactive_only(self):
        offenders = set()
        for route in _api_routes():
            if _body_fields(route) & INTERACTIVE_ONLY_FIELDS:
                for method in route.methods - {'HEAD'}:
                    offenders.add((method, _norm(route.path)))
        assert ('PUT', '/api/email-accounts/{}/send-permission') in offenders
        missing = offenders - _covered()
        assert not missing, (
            'These routes accept an interactive-only field in their body '
            'but are not in INTERACTIVE_ONLY_ROUTES — an agent could set '
            f'it through them: {sorted(missing)}'
        )

    def test_every_handler_writing_the_field_is_interactive_only(self):
        offenders = set()
        for route in _api_routes():
            source = inspect.getsource(route.endpoint)
            if any(p.search(source) for p in _ASSIGNS):
                for method in route.methods - {'HEAD'}:
                    offenders.add((method, _norm(route.path)))
        assert offenders, 'the detector must find the send-permission route'
        missing = offenders - _covered()
        assert not missing, sorted(missing)

    def test_every_entry_targets_a_real_route(self):
        real = {
            (method, _norm(route.path))
            for route in _api_routes()
            for method in route.methods - {'HEAD'}
        }
        stale = _covered() - real
        assert not stale, sorted(stale)

    def test_external_mcp_never_calls_these_handlers(self):
        """The /api/mcp adapters call route functions in-process, which
        skips every middleware — so none may call an interactive-only
        handler."""
        handlers = {
            route.endpoint.__name__
            for route in _api_routes()
            for method in route.methods
            if matching_route(method, re.sub(r'\{[^/]+\}', 'x', route.path))
        }
        assert 'set_send_permission' in handlers
        mcp_dir = Path(__file__).resolve().parents[2] / 'app' / 'agent_mcp'
        sources = '\n'.join(p.read_text() for p in mcp_dir.glob('*.py'))
        assert 'update_email_account' in sources  # the scan reads real code
        used = {name for name in handlers if re.search(rf'\b{name}\b', sources)}
        assert not used, sorted(used)


# ── middleware mechanics ─────────────────────────────────────────


def _scope(path=PATH, method='PUT', headers=()):
    return {
        'type': 'http',
        'asgi': {'version': '3.0'},
        'http_version': '1.1',
        'scheme': 'http',
        'method': method,
        'path': path,
        'raw_path': path.encode(),
        'query_string': b'',
        'headers': list(headers),
    }


async def _receive():
    return {'type': 'http.request', 'body': b'', 'more_body': False}


class _RecordingApp:
    def __init__(self):
        self.called = False

    async def __call__(self, scope, receive, send):
        self.called = True
        await send({
            'type': 'http.response.start',
            'status': 200,
            'headers': [(b'content-type', b'application/json')],
        })
        await send({'type': 'http.response.body', 'body': b'{}'})


async def _run(scope):
    downstream = _RecordingApp()
    out = []

    async def send(message):
        out.append(message)

    await InteractiveOnlyMiddleware(downstream)(scope, _receive, send)
    return downstream.called, out


def _cookie(value: str):
    return (b'cookie', f'auth_token={value}'.encode())


def _bearer(value: str):
    return (b'authorization', f'Bearer {value}'.encode())


HUMAN_JWT = create_token('user-1', 'admin')
BOT_JWT = create_token(AI_BOT_USER_ID, 'ai_bot')


class TestMiddleware:
    @pytest.mark.parametrize(
        'headers',
        [
            pytest.param((_bearer('vst_scratch'),), id='vst-bearer'),
            pytest.param((_cookie('vst_scratch'),), id='vst-cookie'),
            pytest.param((_bearer(HUMAN_JWT),), id='jwt-bearer'),
            pytest.param((_cookie(BOT_JWT),), id='ai-bot-cookie'),
            pytest.param(
                (_cookie(HUMAN_JWT), _bearer(HUMAN_JWT)),
                id='session-cookie-plus-bearer',
            ),
            pytest.param(((b'authorization', b'Basic abc'),), id='any-auth'),
        ],
    )
    async def test_machine_credentials_are_refused(self, headers):
        called, out = await _run(_scope(headers=headers))
        assert not called
        assert out[0]['status'] == 403
        body = json.loads(out[1]['body'])
        assert 'Only the user can change this' in body['detail']
        assert 'Settings → Email' in body['detail']
        assert '"Email sending permission"' in body['detail']

    @pytest.mark.parametrize(
        'headers',
        [
            pytest.param((_cookie(HUMAN_JWT),), id='web-ui-session'),
            pytest.param((), id='no-credential'),
            # Not a valid JWT: proves nothing; the route 401s it when
            # login is on.
            pytest.param((_cookie('garbage'),), id='invalid-cookie'),
        ],
    )
    async def test_interactive_callers_pass(self, headers):
        called, out = await _run(_scope(headers=headers))
        assert called
        assert out[0]['status'] == 200

    async def test_other_routes_untouched(self):
        called, _ = await _run(
            _scope(
                path='/api/email-accounts/acct-1',
                headers=(_bearer(HUMAN_JWT),),
            )
        )
        assert called

    async def test_the_method_must_match(self):
        called, _ = await _run(
            _scope(method='GET', headers=(_bearer('vst_scratch'),))
        )
        assert called

    def test_predicate_reads_header_case_insensitively(self):
        assert presents_machine_credential(
            _scope(headers=((b'Authorization', b'Bearer x'),))
        )
