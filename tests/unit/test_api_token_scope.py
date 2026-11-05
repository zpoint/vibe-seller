"""The token SCOPE guard: routes an API token may never touch.

Three jobs, mirroring ``test_api_token_secrets.py`` on the secret
contract:

* the WRITE drift guard — every REST route whose handler writes a
  credential (``password_hash``, a ``role`` assignment, ``User`` row
  create/delete) is refused in ``TOKEN_REFUSALS``. A new such route
  fails here until it is curated — and the middleware refuses it
  regardless, so curation pins, it does not protect;
* the table stays honest — every entry targets a real route;
* the middleware mechanics, on a scratch ASGI app — a presented
  ``vst_`` credential is 403'd before the app runs, other callers
  and other routes pass through untouched.
"""

import inspect
import json
import re

import pytest

from app.api_token_scope import (
    TOKEN_REFUSALS,
    ApiTokenScopeMiddleware,
    matching_refusal,
)
from app.main import app

pytestmark = pytest.mark.unit

_TOKEN_HEADER = (b'authorization', b'Bearer vst_scratch-token')


def _route_views(routes):
    """Flatten the app's route tree (same duck-typed walk as
    ``test_api_token_secrets.py`` — the lazy ``_IncludedRouter``
    wrapper hides plain APIRoutes behind candidates)."""
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


# Source shapes a credential-WRITING handler must contain one of.
# Reads match none of them: ``user.role ==`` fails the negative
# lookahead on ``=``, ``db.get(User, …)``/``select(User)`` carry no
# ``User(``, and a ``verify_password(…, user.password_hash)`` read has
# no assignment ``=`` after the name.
_CREDENTIAL_WRITE_PATTERNS = (
    re.compile(r'password_hash\s*=[^=]'),
    re.compile(r'hash_password\('),
    re.compile(r'User\('),
    re.compile(r'\.role\s*=[^=]'),
    re.compile(r'db\.delete\(user\)'),
)


def _credential_writing_routes() -> set[tuple[str, str]]:
    found = set()
    for route in _api_routes():
        source = inspect.getsource(route.endpoint)
        if any(pattern.search(source) for pattern in _CREDENTIAL_WRITE_PATTERNS):
            for method in route.methods - {'HEAD'}:
                found.add((method, _norm(route.path)))
    return found


class TestWriteDriftGuard:
    def test_every_credential_writing_route_is_refused(self):
        covered = {
            (method, _norm(refusal.template))
            for refusal in TOKEN_REFUSALS
            for method in refusal.methods
        }
        missing = _credential_writing_routes() - covered
        assert not missing, (
            'These routes WRITE a credential (password_hash / role / '
            'User rows) but no TOKEN_REFUSALS entry refuses an API-'
            'token caller — a leaked token could plant a login that '
            f'survives its revocation: {sorted(missing)}'
        )

    def test_every_refusal_targets_a_real_route(self):
        real = {
            (method, _norm(route.path))
            for route in _api_routes()
            for method in route.methods - {'HEAD'}
        }
        stale = {
            (method, _norm(refusal.template))
            for refusal in TOKEN_REFUSALS
            for method in refusal.methods
        } - real
        assert not stale, (
            f'refusals for routes that do not exist: {sorted(stale)}'
        )

    def test_the_known_credential_routes_are_all_caught(self):
        """The drift detector itself is pinned: the four routes this
        guard was built for (user create/update/delete, own-password
        change) are found by the source patterns AND refused."""
        writing = _credential_writing_routes()
        for expected in (
            ('POST', '/api/users'),
            ('PUT', '/api/users/{}'),
            ('DELETE', '/api/users/{}'),
            ('PATCH', '/api/auth/me/password'),
        ):
            assert expected in writing, expected
            assert matching_refusal(*expected) is not None


# ── middleware mechanics on a scratch ASGI app ───────────────────


def _scope(
    path='/api/users',
    method='POST',
    headers=(),
):
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


def _receive(body=b''):
    async def receive():
        return {
            'type': 'http.request',
            'body': body,
            'more_body': False,
        }

    return receive


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


def _messages():
    out = []

    async def send(message):
        out.append(message)

    return out, send


def _json_of(messages) -> dict:
    body = b''.join(
        m.get('body', b'')
        for m in messages
        if m['type'] == 'http.response.body'
    )
    return json.loads(body)


class TestMiddleware:
    async def test_a_token_caller_is_refused_before_the_app(self):
        downstream = _RecordingApp()
        out, send = _messages()
        middleware = ApiTokenScopeMiddleware(downstream)
        await middleware(
            _scope(headers=(_TOKEN_HEADER,)), _receive(), send
        )
        assert not downstream.called
        assert out[0]['status'] == 403
        assert 'sign in to the web UI' in _json_of(out)['detail']

    async def test_a_cookie_presented_token_is_refused_too(self):
        downstream = _RecordingApp()
        out, send = _messages()
        middleware = ApiTokenScopeMiddleware(downstream)
        await middleware(
            _scope(
                method='DELETE',
                path='/api/users/some-id',
                headers=((b'cookie', b'auth_token=vst_cookie-token'),),
            ),
            _receive(),
            send,
        )
        assert not downstream.called
        assert out[0]['status'] == 403

    async def test_a_session_caller_passes_through(self):
        downstream = _RecordingApp()
        out, send = _messages()
        middleware = ApiTokenScopeMiddleware(downstream)
        await middleware(
            _scope(
                headers=(
                    (b'cookie', b'auth_token=some-jwt-value'),
                    (b'authorization', b'Bearer some-jwt-value'),
                )
            ),
            _receive(),
            send,
        )
        assert downstream.called
        assert out[0]['status'] == 200

    async def test_a_no_credential_caller_passes_through(self):
        downstream = _RecordingApp()
        middleware = ApiTokenScopeMiddleware(downstream)
        out, send = _messages()
        await middleware(_scope(), _receive(), send)
        assert downstream.called

    async def test_an_unrefused_route_passes_a_token_through(self):
        """Reading users is not user management — the refusal is a
        route table, not a blanket: the auth resolution downstream
        decides what a token may READ."""
        downstream = _RecordingApp()
        middleware = ApiTokenScopeMiddleware(downstream)
        out, send = _messages()
        await middleware(
            _scope(method='GET', headers=(_TOKEN_HEADER,)),
            _receive(),
            send,
        )
        assert downstream.called

    async def test_the_method_must_match(self):
        downstream = _RecordingApp()
        middleware = ApiTokenScopeMiddleware(downstream)
        out, send = _messages()
        await middleware(
            _scope(method='PATCH', headers=(_TOKEN_HEADER,)),
            _receive(),
            send,
        )
        assert downstream.called

    async def test_non_http_scopes_pass_through(self):
        seen = []

        async def send(message):
            seen.append(message)

        async def receive():
            return {'type': 'lifespan.startup'}

        downstream_calls = []

        async def downstream(scope, receive, send):
            downstream_calls.append(scope['type'])

        middleware = ApiTokenScopeMiddleware(downstream)
        await middleware({'type': 'lifespan'}, receive, send)
        assert downstream_calls == ['lifespan']
