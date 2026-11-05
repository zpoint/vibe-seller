"""The routes a ``vst_`` API token may never touch.

THE INVARIANT: a token must never be able to turn itself into
another credential — not a new API token, not a session JWT, not a
login — and never manage the users those credentials belong to.
Either side of that would survive revoking (or the expiry of) the
token that produced it, so "revoke the leaked token" would stop
meaning anything. The PR's own contract says the same thing in one
line: a token acts as its owner for *everything except user
management*.

Enforcement point: ONE middleware and ONE table (``TOKEN_REFUSALS``),
not a helper each route must remember to call. The refusal is a
property of the CALLER's credential class, not of individual routes —
the same shape as ``app/api_token_secrets.py`` on the REST secret
contract, and for the same reason: a per-route ``refuse_api_token()``
keeps the rule true only as long as every route author opts in and a
test enumerates them. Here a new user-management route is refused
without anybody doing anything — the drift guard
(``tests/unit/test_api_token_scope.py``) PINS the coverage, it does
not provide it.

Keyed on PRESENTATION (any ``vst_`` credential, wherever presented —
Bearer header or cookie), exactly like the secret middleware's
request side: when the answer is "no", the token's validity is
irrelevant, and resolving it first would only let an invalid token
learn the rule by a different error code.

TRADE-OFF, stated plainly: because the check is presentation-based, a
token that is INVALID (unknown, revoked, expired) is answered 403 on
these routes too, not 401. Access is cut either way — which is what
"revocation means revocation" requires — and the refusal leaks
nothing about the token's validity (a resolution-based check would
need the route layer, i.e. the per-route helper this table replaces,
and would let an anonymous caller probe which tokens exist). The
ordinary 401 path is unchanged: every route that is NOT in the table
still resolves the credential and answers 401 for a bad one.
"""

from dataclasses import dataclass
import json
import re

from app.api_token_secrets import _presented_api_token, compile_template


@dataclass(frozen=True)
class TokenRefusal:
    """One route (by method + path template) a token caller is refused on.

    *template* uses the route's own path with ``{param}`` for path
    params; *action* names the refused action in the 403's wording
    ("An API token cannot {action} — sign in to the web UI…").
    """

    methods: frozenset[str]
    template: str
    action: str
    pattern: 're.Pattern[str]'

    def matches(self, method: str, path: str) -> bool:
        return method in self.methods and bool(self.pattern.fullmatch(path))


def _refusal(methods: set[str], template: str, action: str) -> TokenRefusal:
    return TokenRefusal(
        methods=frozenset(methods),
        template=template,
        action=action,
        pattern=re.compile(compile_template(template)),
    )


TOKEN_REFUSALS: tuple[TokenRefusal, ...] = (
    # User management — out of scope for tokens wholesale: a token
    # that could create or edit a user (password, role) plants a
    # durable login that survives its own revocation; deleting users
    # is the same management surface. Pinned by the write drift
    # guard: every route whose handler writes password_hash / role /
    # User rows must be refused here.
    _refusal({'POST'}, '/api/users', 'create users'),
    _refusal({'PUT'}, '/api/users/{user_id}', 'update users'),
    _refusal({'DELETE'}, '/api/users/{user_id}', 'delete users'),
    # Own-password changes mint a credential the token holder knows.
    _refusal({'PATCH'}, '/api/auth/me/password', 'change passwords'),
    # Credential issuance: a fresh API token or a session JWT would
    # outlive the credential that produced it.
    _refusal({'POST'}, '/api/auth/tokens', 'mint API tokens'),
    _refusal({'POST'}, '/api/auth/refresh', 'start a web session'),
)

_DETAIL = 'An API token cannot {action} — sign in to the web UI to do this'


async def _send_json(send, status: int, payload: dict) -> None:
    """Answer the request ourselves (the scope refusal)."""
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


def matching_refusal(method: str, path: str) -> TokenRefusal | None:
    """The refusal rule matching this (method, path), if any."""
    for refusal in TOKEN_REFUSALS:
        if refusal.matches(method, path):
            return refusal
    return None


class ApiTokenScopeMiddleware:
    """Pure ASGI. Refuses token-presented calls on ``TOKEN_REFUSALS``.

    Registered OUTSIDE ``ApiTokenSecretMiddleware`` (added after it —
    ``add_middleware`` front-inserts) so a refused request never has
    its body parsed for the restore contract, and inside CORS so the
    403 reaches cross-origin callers as a readable status.
    """

    def __init__(
        self, app, refusals: tuple[TokenRefusal, ...] = TOKEN_REFUSALS
    ):
        self.app = app
        self.refusals = refusals

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http' and _presented_api_token(scope):
            refusal = matching_refusal(scope['method'].upper(), scope['path'])
            if refusal is not None:
                await _send_json(
                    send, 403, {'detail': _DETAIL.format(action=refusal.action)}
                )
                return
        await self.app(scope, receive, send)
