"""Routes only a person at the web UI may call.

THE INVARIANT: a setting that exists to hold an agent back (today: a
mailbox's ``allow_send``) can be changed only by the user, signed in to
the web UI — never by an agent, whichever credential the agent holds.

Agents hold three kinds of credential, and each is refused here:

* a ``vst_`` API token (external agents), presented as a Bearer header
  or as the cookie;
* a JWT presented as a Bearer header — the browser-use wrapper scripts
  carry one, and a task agent with a shell can read and replay it;
* the AI-bot JWT (role ``ai_bot``) the in-task MCP server presents as
  the cookie — a task agent can read that one too, and replay it as a
  cookie, so the cookie transport alone does not prove a person.

What passes is the web UI's own session cookie (a JWT for a human
user, set httpOnly by ``/api/auth/login`` — no agent process is handed
one) and, on a server with login turned OFF, a request with no
credential at all.

THE LIMIT, stated plainly: with login turned off there is no credential
to tell the web UI from an agent's ``curl`` with no credential — both
resolve to the default admin. So these routes are agent-proof only when
login is on. Documented for users in docs/email.md / email.en.md.

Enforcement point: ONE middleware and ONE table
(``INTERACTIVE_ONLY_ROUTES``), keyed on how the credential is
PRESENTED (plus the cookie JWT's role claim), the same shape as
``app/api_token_scope.py``. A sibling of that middleware rather than a
second table inside it because the rule differs: the token scope
refuses only ``vst_`` presentations (a JWT Bearer is the browser
wrapper's legitimate credential everywhere else), while this refuses
every machine credential. One table per rule keeps each predicate and
its drift guard (``tests/unit/test_interactive_only.py``) simple.
"""

import json
import re

import jwt

from app.api_tokens import TOKEN_PREFIX
from app.config import JWT_SECRET
from app.rest_secret_guard import compile_template

_COOKIE_NAME = b'auth_token'
_AI_BOT_ROLE = 'ai_bot'

_DETAIL = (
    'Only the user can change this, signed in to the web UI ({where}). '
    'An agent cannot change it, whatever credential it uses — tell the '
    'user what you need and let them decide.'
)


class InteractiveOnlyRoute:
    """One route (method + path template) refused to machine callers."""

    def __init__(self, methods: set[str], template: str, where: str):
        self.methods = frozenset(methods)
        self.template = template
        self.where = where
        self.pattern = re.compile(compile_template(template))

    def matches(self, method: str, path: str) -> bool:
        return method in self.methods and bool(self.pattern.fullmatch(path))


INTERACTIVE_ONLY_ROUTES: tuple[InteractiveOnlyRoute, ...] = (
    # The per-mailbox send gate (app/email/sender.py). An agent that
    # could flip it would make the gate moot.
    InteractiveOnlyRoute(
        {'PUT'},
        '/api/email-accounts/{account_id}/send-permission',
        'Settings → Email, "Email sending permission" switch on that mailbox',
    ),
)

# Body fields that only an INTERACTIVE_ONLY route may accept. Pinned by
# the drift guard: a request model of any other route that grows one of
# these fields fails the test — that would be a second door to the gate.
INTERACTIVE_ONLY_FIELDS = frozenset({'allow_send'})


def matching_route(method: str, path: str) -> InteractiveOnlyRoute | None:
    for route in INTERACTIVE_ONLY_ROUTES:
        if route.matches(method, path):
            return route
    return None


def _cookie(headers: dict[bytes, bytes]) -> bytes:
    for part in headers.get(b'cookie', b'').split(b';'):
        name, _, value = part.strip().partition(b'=')
        if name == _COOKIE_NAME:
            return value
    return b''


def presents_machine_credential(scope: dict) -> bool:
    """Whether this request carries any credential an agent holds.

    Any ``Authorization`` header (a ``vst_`` token or a JWT); a ``vst_``
    cookie; or a cookie JWT whose role is the AI bot. A cookie that is
    not a valid JWT is left to the route, which 401s it when login is
    on — it proves nothing either way.
    """
    headers = {key.lower(): value for key, value in scope.get('headers') or []}
    if headers.get(b'authorization'):
        return True
    cookie = _cookie(headers)
    if not cookie:
        return False
    if cookie.startswith(TOKEN_PREFIX.encode()):
        return True
    try:
        claims = jwt.decode(
            cookie.decode('ascii', 'replace'), JWT_SECRET, algorithms=['HS256']
        )
    except jwt.PyJWTError:
        return False
    return claims.get('role') == _AI_BOT_ROLE


async def _refuse(send, detail: str) -> None:
    body = json.dumps({'detail': detail}).encode('utf-8')
    await send({
        'type': 'http.response.start',
        'status': 403,
        'headers': [
            (b'content-type', b'application/json'),
            (b'content-length', str(len(body)).encode('ascii')),
        ],
    })
    await send({'type': 'http.response.body', 'body': body})


class InteractiveOnlyMiddleware:
    """Pure ASGI. Refuses machine credentials on INTERACTIVE_ONLY_ROUTES."""

    def __init__(
        self,
        app,
        routes: tuple[InteractiveOnlyRoute, ...] = INTERACTIVE_ONLY_ROUTES,
    ):
        self.app = app
        self.routes = routes

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            method = scope['method'].upper()
            route = next(
                (r for r in self.routes if r.matches(method, scope['path'])),
                None,
            )
            if route is not None and presents_machine_credential(scope):
                await _refuse(send, _DETAIL.format(where=route.where))
                return
        await self.app(scope, receive, send)
