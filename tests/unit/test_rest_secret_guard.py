"""The REST-side secret guard — for EVERY caller.

Four jobs:

* the READ drift guard — every REST route whose response model
  carries a secret-classified field is in ``SECRET_READ_ROUTES``
  (curated below; the workflow twin pins the actual behaviour:
  masked for a session caller AND a token caller, or a deliberate
  disclosure). A new secret-returning route fails here until it is
  curated — and the middleware masks it regardless, so curation
  pins, it does not protect;
* the WRITE drift guard — every REST route whose BODY carries a
  secret-classified field (pydantic body models introspected from
  the real app, plus the plain-dict bodies listed alongside) has a
  restore rule in ``RESTORE_RULES``: a masked echo must be restored
  (update) or refused (create), never stored;
* the DISCLOSURE drift guard — the routes allowed to hand a
  credential out in plaintext (``mark_secret_disclosure``) are
  curated in ``SECRET_DISCLOSURE_ROUTES`` and must be real routes;
* the middleware mechanics, on a scratch ASGI app — what is redacted
  (every JSON response, whatever the caller), what passes untouched
  (SSE/streaming, non-JSON, file deliveries, disclosed responses),
  and the request path (restore, empty-means-keep, refusal, replay).
"""

import inspect
from inspect import isclass
import json
import re
from typing import get_args, get_origin

from pydantic import BaseModel
import pytest
from starlette.datastructures import State

from app.agent_mcp.redact import is_secret_key
from app.main import app
from app.rest_secret_guard import (
    RESTORE_RULES,
    RestoreRule,
    SecretGuardMiddleware,
    compile_template,
    mark_secret_disclosure,
)

pytestmark = pytest.mark.unit

SECRET = 'super-secret-value-1234'

# Every REST route whose response payload carries a secret-classified
# field, mapped to what callers receive:
#
# * 'masked' — every caller (session, token, no-auth) receives the
#   field masked; the middleware provides it, the workflow tests pin
#   it for each credential class;
# * 'refused' — an API-token caller is refused outright (403), so the
#   field only exists for interactive callers;
# * 'disclosed' — a DELIBERATE plaintext disclosure to its allowed
#   caller: the route calls ``mark_secret_disclosure`` (mint hands
#   the fresh token to its creator once) or is the admin-only reveal
#   endpoint itself.
SECRET_READ_ROUTES = {
    ('GET', '/api/profiles'): 'masked',
    ('POST', '/api/profiles'): 'masked',
    ('PUT', '/api/profiles/{profile_id}'): 'masked',
    # The chat-bot single GET + create/update return the raw webhook
    # URL (it embeds the key) and the signing secret for the edit
    # form; ChatBotSummary (list) is already mask-only.
    ('GET', '/api/chat-bots/{bot_id}'): 'masked',
    ('POST', '/api/chat-bots'): 'masked',
    ('PUT', '/api/chat-bots/{bot_id}'): 'masked',
    # The mint response is the one REST payload that carries a FRESH
    # credential; a token caller is refused (refuse_api_token) rather
    # than shown it masked — a new token must never beget another —
    # and the creating session receives it once (disclosed).
    ('POST', '/api/auth/tokens'): 'disclosed',
    # The admin reveal endpoint IS a disclosure — that is its whole
    # purpose (members and every token caller are 403).
    ('POST', '/api/secrets/reveal'): 'disclosed',
}

# Routes whose body is a plain dict carrying secret-classified fields
# — nothing to introspect, so they are listed by hand. Their handlers
# reading those fields is checked by the write guard's source check.
SECRET_DICT_BODY_ROUTES = {
    ('POST', '/api/profiles'),
    ('PUT', '/api/profiles/{profile_id}'),
    ('POST', '/api/profiles/validate'),
    ('PUT', '/api/vision/config'),
}

# The routes that deliberately disclose a credential in plaintext by
# calling ``mark_secret_disclosure``. Both entries are pinned by the
# workflow tests (mint → plaintext for the creating session; reveal
# → plaintext for an admin session, 403 for everyone else).
SECRET_DISCLOSURE_ROUTES = {
    ('POST', '/api/auth/tokens'),
    ('POST', '/api/secrets/reveal'),
}


def _route_views(routes):
    """Flatten the app's route tree into introspectable route views.

    This FastAPI wraps every ``include_router`` in a lazy
    ``_IncludedRouter`` whose *effective candidate contexts* carry the
    path/methods/models (plugin-aware); plain APIRoutes sit on the
    app directly. Duck-typed on purpose — the wrapper type is private
    to FastAPI and may change again.
    """
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
    """A path template with its params normalised, so a rule's
    ``{profile_id}`` compares equal to the route's own spelling."""
    return re.sub(r'\{[^/]+\}', '{}', path)


def _secret_fields(model) -> list[str]:
    origin = get_origin(model)
    if origin is not None:
        args = get_args(model)
        model = args[0] if args else None
    if not (isclass(model) and issubclass(model, BaseModel)):
        return []
    return [f for f in model.model_fields if is_secret_key(f)]


class TestReadDriftGuard:
    def test_every_secret_response_route_is_curated(self):
        found = set()
        for route in _api_routes():
            for field in _secret_fields(route.response_model):
                for method in route.methods - {'HEAD'}:
                    found.add((method, route.path))
                break  # one secret field is enough to curate the route
        missing = found - set(SECRET_READ_ROUTES)
        assert not missing, (
            'These routes return a secret-classified field but are '
            'not in SECRET_READ_ROUTES. The middleware masks them '
            'already; curate them so the workflow tests pin the '
            f'behaviour: {sorted(missing)}'
        )

    def test_curated_routes_are_real_routes(self):
        real = {
            (method, route.path)
            for route in _api_routes()
            for method in route.methods - {'HEAD'}
        }
        stale = set(SECRET_READ_ROUTES) - real
        assert not stale, f'stale curated entries: {sorted(stale)}'

    def test_the_dict_payload_routes_exist(self):
        """The profile routes return plain dicts (no response_model to
        introspect) — their entries are real routes or the curation
        rots silently."""
        real = {
            (method, route.path)
            for route in _api_routes()
            for method in route.methods - {'HEAD'}
        }
        missing = SECRET_DICT_BODY_ROUTES - real
        assert not missing, f'stale dict-body entries: {sorted(missing)}'


def _body_models_of(endpoint) -> list[type[BaseModel]]:
    """The pydantic body models of a route handler, from its own
    signature — stable across FastAPI internals (this FastAPI's
    ModelField shim no longer carries the annotation)."""
    models = []
    for param in inspect.signature(endpoint).parameters.values():
        ann = param.annotation
        if isclass(ann) and issubclass(ann, BaseModel):
            models.append(ann)
    return models


class TestWriteDriftGuard:
    def _secret_body_routes(self) -> set[tuple[str, str]]:
        found = set()
        for route in _api_routes():
            for model in _body_models_of(route.endpoint):
                for _field in _secret_fields(model):
                    for method in route.methods - {'HEAD'}:
                        found.add((method, route.path))
                    break
        return found

    def test_every_secret_body_route_has_a_restore_rule(self):
        covered = {
            (method, _norm(rule.template))
            for rule in RESTORE_RULES
            for method in rule.methods
        }
        required = {
            (method, _norm(path))
            for method, path in (
                self._secret_body_routes() | SECRET_DICT_BODY_ROUTES
            )
        }
        missing = required - covered
        assert not missing, (
            'These routes ACCEPT a secret-classified body field but no '
            'RESTORE_RULES entry restores a masked echo — an echoed '
            f'mask would be stored verbatim: {sorted(missing)}'
        )

    def test_every_rule_targets_a_real_route(self):
        real = {
            (method, _norm(route.path))
            for route in _api_routes()
            for method in route.methods - {'HEAD'}
        }
        stale = {
            (method, _norm(rule.template))
            for rule in RESTORE_RULES
            for method in rule.methods
        } - real
        assert not stale, (
            'rules for routes that do not exist (moved? renamed?): '
            f'{sorted(stale)}'
        )

    def test_rule_providers_are_async_and_named_like_the_resource(self):
        """A rule whose provider is not awaitable would 500 on every
        matching write; a nameless inline lambda would make the table
        unreadable in the 400 logs."""
        for rule in RESTORE_RULES:
            assert inspect.iscoroutinefunction(rule.stored), rule.template
            assert rule.stored.__name__ not in ('<lambda>',), rule.template


class TestDisclosureDriftGuard:
    def test_disclosure_routes_are_real_routes(self):
        real = {
            (method, route.path)
            for route in _api_routes()
            for method in route.methods - {'HEAD'}
        }
        stale = SECRET_DISCLOSURE_ROUTES - real
        assert not stale, f'stale disclosure entries: {sorted(stale)}'

    def test_the_mint_and_reveal_routes_are_both_disclosures(self):
        """The two deliberate plaintext hand-outs — the fresh token to
        its creator, and the reveal endpoint — must stay in the
        curated set; anything else arriving here changes what the
        workflow tests must pin."""
        assert ('POST', '/api/auth/tokens') in SECRET_DISCLOSURE_ROUTES
        assert ('POST', '/api/secrets/reveal') in SECRET_DISCLOSURE_ROUTES

    def test_no_other_route_marks_itself_a_disclosure(self):
        """A route that marks itself a disclosure BYPASSES the mask
        for every caller — so the set of routes allowed to do that is
        pinned from the SOURCE side too, not only the curated list.

        Without this, a new route could call
        ``mark_secret_disclosure`` and hand a stored secret to any
        caller, while the curated list (and the workflow pins) still
        look right.
        """
        marking_routes = set()
        for route in _api_routes():
            source = inspect.getsource(route.endpoint)
            if 'mark_secret_disclosure' in source:
                marking_routes.add((
                    next(iter(route.methods - {'HEAD'})),
                    route.path,
                ))
        assert marking_routes == SECRET_DISCLOSURE_ROUTES, (
            'These routes mark their response as a deliberate '
            'plaintext disclosure but are not in '
            f'SECRET_DISCLOSURE_ROUTES: {sorted(marking_routes)}'
        )


# ── middleware mechanics on a scratch ASGI app ───────────────────


class _FakeStateRequest:
    """Just enough Request for ``mark_secret_disclosure``."""

    def __init__(self):
        self.scope = {}
        self.state = State(self.scope.setdefault('state', {}))


def _scope(
    path='/api/profiles',
    method='GET',
    headers=(),
    auth_via=None,
):
    scope = {
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
    if auth_via is not None:
        scope['state'] = {'auth_via': auth_via}
    return scope


def _receive(body=b''):
    async def receive():
        return {
            'type': 'http.request',
            'body': body,
            'more_body': False,
        }

    return receive


def _messages():
    out = []

    async def send(message):
        out.append(message)

    return out, send


def _json_app(payload, auth_via=None, status=200, disclose=False):
    async def app(scope, receive, send):
        state = scope.setdefault('state', {})
        if auth_via:
            state['auth_via'] = auth_via
        if disclose:
            state['discloses_secret'] = True
        body = json.dumps(payload).encode()
        await send({
            'type': 'http.response.start',
            'status': status,
            'headers': [
                (b'content-type', b'application/json'),
                (b'content-length', str(len(body)).encode()),
            ],
        })
        await send({'type': 'http.response.body', 'body': body})

    return app


def _body_of(messages) -> bytes:
    return b''.join(
        m.get('body', b'')
        for m in messages
        if m['type'] == 'http.response.body'
    )


def _header_of(messages, name: bytes):
    for m in messages:
        if m['type'] == 'http.response.start':
            return dict(m['headers']).get(name)
    return None


class TestResponseRedaction:
    async def test_json_is_redacted_for_every_caller(self):
        """Session callers (the web UI), token callers and no-auth
        callers all receive the same masks — the invariant is a
        property of the response, not of the credential class."""
        payload = {
            'profiles': [
                {
                    'id': 'acme',
                    'env': {'ANTHROPIC_AUTH_TOKEN': SECRET},
                }
            ]
        }
        for via in ('session', 'api_token', 'service', 'no_auth', None):
            out, send = _messages()
            middleware = SecretGuardMiddleware(_json_app(payload, auth_via=via))
            await middleware(_scope(auth_via=via), _receive(), send)
            data = json.loads(_body_of(out))
            token = data['profiles'][0]['env']['ANTHROPIC_AUTH_TOKEN']
            assert token != SECRET, via
            assert token == f'****{SECRET[-4:]}', via

    async def test_a_marked_disclosure_passes_untouched(self):
        """A route that hands out a credential it just created (the
        token mint; the reveal endpoint) flags itself — its response
        is the one deliberate plaintext disclosure."""
        out, send = _messages()
        middleware = SecretGuardMiddleware(
            _json_app({'token': SECRET}, disclose=True)
        )
        await middleware(_scope(), _receive(), send)
        assert json.loads(_body_of(out)) == {'token': SECRET}

    def test_the_marker_writes_through_the_request_state(self):
        """``mark_secret_disclosure`` writes into the scope's state
        dict — the same shared dict the middleware reads at response
        time (the ``auth_via`` mechanism)."""
        request = _FakeStateRequest()
        mark_secret_disclosure(request)
        assert request.scope['state']['discloses_secret'] is True

    async def test_content_length_is_recomputed(self):
        out, send = _messages()
        middleware = SecretGuardMiddleware(_json_app({'secret_key': SECRET}))
        await middleware(_scope(), _receive(), send)
        body = _body_of(out)
        assert _header_of(out, b'content-length') == str(len(body)).encode()

    async def test_streaming_sse_passes_chunk_by_chunk(self):
        async def app(scope, receive, send):
            await send({
                'type': 'http.response.start',
                'status': 200,
                'headers': [(b'content-type', b'text/event-stream')],
            })
            await send({
                'type': 'http.response.body',
                'body': b'data: {"secret": "abc"}',
                'more_body': True,
            })
            await send({
                'type': 'http.response.body',
                'body': b'\n\n',
                'more_body': False,
            })

        out, send = _messages()
        await SecretGuardMiddleware(app)(_scope(), _receive(), send)
        # Chunk boundaries and bytes preserved exactly.
        assert [m.get('body') for m in out[1:]] == [
            b'data: {"secret": "abc"}',
            b'\n\n',
        ]

    async def test_a_file_delivery_with_a_json_media_type_passes(self):
        """A task's config.json output downloads with a JSON media
        type AND a content-disposition — it is a file, not an API
        payload, and must arrive byte-for-byte."""

        async def app(scope, receive, send):
            body = b'{"api_key": "sk-live-unchanged"}'
            await send({
                'type': 'http.response.start',
                'status': 200,
                'headers': [
                    (b'content-type', b'application/json'),
                    (
                        b'content-disposition',
                        b'attachment; filename="config.json"',
                    ),
                ],
            })
            await send({'type': 'http.response.body', 'body': body})

        out, send = _messages()
        await SecretGuardMiddleware(app)(_scope(), _receive(), send)
        assert _body_of(out) == b'{"api_key": "sk-live-unchanged"}'

    async def test_non_json_bodies_pass_untouched(self):
        async def app(scope, receive, send):
            await send({
                'type': 'http.response.start',
                'status': 200,
                'headers': [(b'content-type', b'application/zip')],
            })
            await send({'type': 'http.response.body', 'body': b'PK\x03\x04'})

        out, send = _messages()
        await SecretGuardMiddleware(app)(_scope(), _receive(), send)
        assert _body_of(out) == b'PK\x03\x04'

    async def test_json_content_type_with_unparsable_body_passes(self):
        async def app(scope, receive, send):
            await send({
                'type': 'http.response.start',
                'status': 200,
                'headers': [(b'content-type', b'application/json')],
            })
            await send({'type': 'http.response.body', 'body': b'{"broken": '})

        out, send = _messages()
        await SecretGuardMiddleware(app)(_scope(), _receive(), send)
        assert _body_of(out) == b'{"broken": '

    async def test_empty_body_response_survives(self):
        async def app(scope, receive, send):
            await send({
                'type': 'http.response.start',
                'status': 204,
                'headers': [(b'content-type', b'application/json')],
            })
            await send({'type': 'http.response.body', 'body': b''})

        out, send = _messages()
        await SecretGuardMiddleware(app)(_scope(), _receive(), send)
        assert _body_of(out) == b''
        assert out[0]['status'] == 204

    async def test_error_json_is_also_redacted(self):
        """A route that fails may still embed what it was holding —
        the redaction is not success-only."""
        out, send = _messages()
        middleware = SecretGuardMiddleware(
            _json_app(
                {'detail': {'env': {'ANTHROPIC_API_KEY': SECRET}}},
                status=400,
            )
        )
        await middleware(_scope(), _receive(), send)
        assert out[0]['status'] == 400
        data = json.loads(_body_of(out))
        key = data['detail']['env']['ANTHROPIC_API_KEY']
        assert key != SECRET and key.startswith('****')


class _RecordingApp:
    """Scratch downstream app: records the request body it received
    and answers with an empty JSON object."""

    def __init__(self):
        self.bodies: list[bytes] = []
        self.scope = None
        self.called = False

    async def __call__(self, scope, receive, send):
        self.called = True
        self.scope = scope
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            self.bodies.append(message.get('body', b''))
            if not message.get('more_body', False):
                break
        await send({
            'type': 'http.response.start',
            'status': 200,
            'headers': [(b'content-type', b'application/json')],
        })
        await send({'type': 'http.response.body', 'body': b'{}'})


def _rule(template, stored, methods=frozenset({'PUT'})):
    return RestoreRule(
        methods=methods,
        template=template,
        stored=stored,
        pattern=re.compile(compile_template(template)),
    )


_JSON_TYPE = (b'content-type', b'application/json')


class TestRequestRestore:
    async def test_a_masked_echo_is_restored_before_the_route(self):
        async def stored(match, body):
            return {'env': {'ANTHROPIC_AUTH_TOKEN': SECRET}}

        downstream = _RecordingApp()
        middleware = SecretGuardMiddleware(
            downstream, rules=(_rule('/api/profiles/{pid}', stored),)
        )
        body = json.dumps({
            'name': 'acme',
            'env': {'ANTHROPIC_AUTH_TOKEN': '****1234'},
        }).encode()
        out, send = _messages()
        await middleware(
            _scope(
                path='/api/profiles/prof-1',
                method='PUT',
                headers=(
                    _JSON_TYPE,
                    (b'content-length', str(len(body)).encode()),
                ),
            ),
            _receive(body),
            send,
        )
        assert downstream.called
        received = json.loads(b''.join(downstream.bodies))
        assert received['env']['ANTHROPIC_AUTH_TOKEN'] == SECRET
        assert received['name'] == 'acme'
        # the declared length follows the restored body
        lengths = [
            v for k, v in downstream.scope['headers'] if k == b'content-length'
        ]
        assert lengths == [str(len(b''.join(downstream.bodies))).encode()]

    async def test_the_restore_applies_to_session_callers_too(self):
        """No credential is presented at all (the session UI, or a
        no-auth server) — the mask the form echoes is still restored,
        because nobody can hold plaintext any more."""

        async def stored(match, body):
            return {'env': {'ANTHROPIC_AUTH_TOKEN': SECRET}}

        downstream = _RecordingApp()
        middleware = SecretGuardMiddleware(
            downstream, rules=(_rule('/api/profiles/{pid}', stored),)
        )
        body = json.dumps({
            'env': {'ANTHROPIC_AUTH_TOKEN': '****1234'}
        }).encode()
        out, send = _messages()
        await middleware(
            _scope(
                path='/api/profiles/prof-1', method='PUT', headers=(_JSON_TYPE,)
            ),
            _receive(body),
            send,
        )
        received = json.loads(b''.join(downstream.bodies))
        assert received['env']['ANTHROPIC_AUTH_TOKEN'] == SECRET

    async def test_an_empty_secret_keeps_the_stored_value(self):
        """An untouched password box posts '' — on an update that
        means "unchanged", never "store an empty"."""

        async def stored(match, body):
            return {'password': SECRET}

        downstream = _RecordingApp()
        middleware = SecretGuardMiddleware(
            downstream, rules=(_rule('/api/ziniao/{aid}', stored),)
        )
        body = json.dumps({'password': ''}).encode()
        out, send = _messages()
        await middleware(
            _scope(path='/api/ziniao/a1', method='PUT', headers=(_JSON_TYPE,)),
            _receive(body),
            send,
        )
        received = json.loads(b''.join(downstream.bodies))
        assert received['password'] == SECRET

    async def test_an_empty_secret_with_nothing_stored_passes_through(self):
        """Create-style bodies may legitimately send '' for an
        optional secret — nothing is stored, so nothing is kept."""

        async def stored(match, body):
            return {}

        downstream = _RecordingApp()
        middleware = SecretGuardMiddleware(
            downstream, rules=(_rule('/api/chat-bots', stored, {'POST'}),)
        )
        body = json.dumps({'name': 'acme', 'secret': ''}).encode()
        out, send = _messages()
        await middleware(
            _scope(path='/api/chat-bots', method='POST', headers=(_JSON_TYPE,)),
            _receive(body),
            send,
        )
        received = json.loads(b''.join(downstream.bodies))
        assert received['secret'] == ''

    async def test_a_mask_with_no_stored_secret_is_refused(self):
        async def stored(match, body):
            return {}

        downstream = _RecordingApp()
        middleware = SecretGuardMiddleware(
            downstream, rules=(_rule('/api/profiles/{pid}', stored),)
        )
        body = json.dumps({'env': {'ANTHROPIC_AUTH_TOKEN': '****'}}).encode()
        out, send = _messages()
        await middleware(
            _scope(
                path='/api/profiles/prof-1', method='PUT', headers=(_JSON_TYPE,)
            ),
            _receive(body),
            send,
        )
        assert not downstream.called
        assert out[0]['status'] == 400
        assert 'real secret' in json.loads(_body_of(out))['detail']

    async def test_a_fresh_secret_passes_through(self):
        async def stored(match, body):
            return {'env': {'ANTHROPIC_AUTH_TOKEN': 'the-old-one'}}

        downstream = _RecordingApp()
        middleware = SecretGuardMiddleware(
            downstream, rules=(_rule('/api/profiles/{pid}', stored),)
        )
        body = json.dumps({
            'env': {'ANTHROPIC_AUTH_TOKEN': 'brand-new'}
        }).encode()
        out, send = _messages()
        await middleware(
            _scope(
                path='/api/profiles/prof-1', method='PUT', headers=(_JSON_TYPE,)
            ),
            _receive(body),
            send,
        )
        received = json.loads(b''.join(downstream.bodies))
        assert received['env']['ANTHROPIC_AUTH_TOKEN'] == 'brand-new'

    async def test_a_rule_may_read_the_stored_values_from_the_body(self):
        """The validate probe carries the edited profile's id in its
        BODY — the provider resolves the stored env through it."""

        async def stored(match, body):
            assert body['profile_id'] == 'acme'
            return {'env': {'ANTHROPIC_AUTH_TOKEN': SECRET}}

        downstream = _RecordingApp()
        middleware = SecretGuardMiddleware(
            downstream,
            rules=(
                _rule(
                    '/api/profiles/validate',
                    stored,
                    methods=frozenset({'POST'}),
                ),
            ),
        )
        body = json.dumps({
            'profile_id': 'acme',
            'env': {'ANTHROPIC_AUTH_TOKEN': '****1234'},
        }).encode()
        out, send = _messages()
        await middleware(
            _scope(
                path='/api/profiles/validate',
                method='POST',
                headers=(_JSON_TYPE,),
            ),
            _receive(body),
            send,
        )
        received = json.loads(b''.join(downstream.bodies))
        assert received['env']['ANTHROPIC_AUTH_TOKEN'] == SECRET

    async def test_non_json_bodies_are_replayed_untouched(self):
        """Whatever the restore cannot walk, the app still receives
        byte-for-byte — consuming receive is irreversible."""

        async def stored(match, body):
            return {}

        downstream = _RecordingApp()
        middleware = SecretGuardMiddleware(
            downstream, rules=(_rule('/api/profiles/{pid}', stored),)
        )
        raw = b'not json at all'
        out, send = _messages()
        await middleware(
            _scope(
                path='/api/profiles/prof-1', method='PUT', headers=(_JSON_TYPE,)
            ),
            _receive(raw),
            send,
        )
        assert downstream.called
        assert b''.join(downstream.bodies) == raw

    async def test_no_rule_no_touch(self):
        downstream = _RecordingApp()
        middleware = SecretGuardMiddleware(downstream, rules=())
        body = json.dumps({'env': {'ANTHROPIC_AUTH_TOKEN': '****'}}).encode()
        out, send = _messages()
        await middleware(
            _scope(path='/api/other', method='PUT', headers=(_JSON_TYPE,)),
            _receive(body),
            send,
        )
        assert b''.join(downstream.bodies) == body


class TestRuleMatching:
    def test_path_params_match_a_segment_not_more(self):
        rule = _rule('/api/chat-bots/{bot_id}', _nothing_like())
        assert rule.matches('PUT', '/api/chat-bots/bot-1')
        assert not rule.matches('PUT', '/api/chat-bots/bot-1/extra')
        assert not rule.matches('GET', '/api/chat-bots/bot-1')

    def test_a_plain_prefix_route_does_not_capture_suffixes(self):
        """POST /api/profiles must not swallow /api/profiles/validate
        (its own rule) nor PUT /api/profiles/x."""
        rule = _rule(
            '/api/profiles', _nothing_like(), methods=frozenset({'POST'})
        )
        assert rule.matches('POST', '/api/profiles')
        assert not rule.matches('POST', '/api/profiles/validate')
        assert not rule.matches('PUT', '/api/profiles')


def _nothing_like():
    async def stored(match, body):
        return {}

    return stored
