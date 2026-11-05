"""Authentication utilities — password hashing, JWT tokens, FastAPI deps."""

from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, Request, Response
import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_tokens import (
    TOKEN_PREFIX,
    find_token,
    is_expired,
    is_revoked,
    touch_last_used,
)
from app.config import DEFAULT_USER_ID, JWT_SECRET
from app.database import get_db
from app.models.app_settings import AppSettings
from app.models.user import User
from app.workspace.actor import (
    actor_for,
    clear_request_actor,
    set_request_actor,
)

ALGORITHM = 'HS256'
COOKIE_NAME = 'auth_token'

# Interactive browser login sessions are short-lived and roll forward on
# real user activity (POST /api/auth/refresh, driven by the frontend
# keepalive). Idle longer than this and the cookie lapses → the client
# is bounced to the login page. Kept deliberately short for security.
USER_SESSION_EXPIRE = timedelta(hours=24)

# Service tokens (ai_bot wrapper auto-start curl, `system`) are NOT
# interactive: they get baked into per-store browser-use wrapper scripts
# and must keep authenticating for long-running / scheduled work that
# can fire well after the wrapper was written. They must outlive a user
# session, so this — not the 24h window — is create_token's default.
SERVICE_TOKEN_EXPIRE = timedelta(days=7)


def create_token(
    user_id: str, role: str, *, expires_delta: timedelta | None = None
) -> str:
    """Sign a JWT. Defaults to the long-lived service-token TTL; pass
    ``expires_delta=USER_SESSION_EXPIRE`` for interactive logins."""
    expire = datetime.now(UTC) + (expires_delta or SERVICE_TOKEN_EXPIRE)
    return jwt.encode(
        {'sub': user_id, 'role': role, 'exp': expire},
        JWT_SECRET,
        algorithm=ALGORITHM,
    )


def set_session_cookie(response: Response, token: str) -> None:
    """Write the interactive-session auth cookie. Single source of truth
    for cookie flags/TTL, shared by login and the sliding refresh."""
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite='lax',
        max_age=int(USER_SESSION_EXPIRE.total_seconds()),
    )


async def is_auth_required(db: AsyncSession) -> bool:
    """Check whether login is required (from DB setting)."""
    result = await db.execute(
        select(AppSettings).where(AppSettings.key == 'auth_required')
    )
    setting = result.scalar_one_or_none()
    if not setting or not setting.value:
        return False
    return setting.value.strip().lower() in ('true', '1')


def _bearer_token(request: Request) -> str:
    """The bearer credential of this request, or '' when absent.

    Used by the browser-use wrapper scripts for auto-start and by
    external agents presenting an API token.
    """
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        return auth_header[7:]
    return ''


async def _authenticate_api_token(db: AsyncSession, plaintext: str) -> User:
    """Authenticate a ``vst_`` personal API token.

    Every failure here is a 401 — including when auth is disabled. The
    token table is the only authority on whether this credential works:
    if a revoked token were allowed to fall through to the
    default-admin path, the owner would believe their access was cut
    when it was not.
    """
    row = await find_token(db, plaintext)
    if row is None:
        raise HTTPException(status_code=401, detail='Invalid API token')
    if is_revoked(row):
        raise HTTPException(status_code=401, detail='API token revoked')
    if is_expired(row):
        raise HTTPException(status_code=401, detail='API token expired')
    user = await db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail='Invalid API token')
    await touch_last_used(db, row)
    return user


async def get_current_user(
    request: Request, db: AsyncSession = Depends(get_db)
):
    """Resolve the caller, and record HOW they authenticated.

    Every successful return sets ``request.state.auth_via`` to one of:

    * ``'api_token'`` — a ``vst_`` personal API token (external agents);
    * ``'session'`` — a JWT presented as the login cookie (the web UI);
    * ``'service'`` — a JWT presented as a Bearer header (the
      browser-use wrapper scripts and other non-interactive callers);
    * ``'no_auth'`` — no credential, on a server with auth turned off
      (the default admin).

    Routes can then refuse credential-escalation with a token (an API
    token must not be able to mint another API token) without inventing
    a second permission model: the transport a JWT arrived by is what
    distinguishes an interactive login from a machine credential.
    That refusal lives in ONE place — ``ApiTokenScopeMiddleware``
    (``app/api_token_scope.py``) — not in per-route helpers.

    The same resolution attributes workspace git commits to the caller
    (``app/workspace/actor.py``): cleared up front — this request does
    not speak for whoever a previous one resolved — and set on every
    successful return.
    """
    clear_request_actor()
    cookie_token = request.cookies.get(COOKIE_NAME, '')
    bearer_token = _bearer_token(request)

    # A credential that starts with ``vst_`` is an API token, wherever
    # it was presented, and is always checked against the token table.
    # The header wins over the cookie: it is the credential the caller
    # chose for this request.
    api_token = next(
        (
            candidate
            for candidate in (bearer_token, cookie_token)
            if candidate.startswith(TOKEN_PREFIX)
        ),
        None,
    )
    if api_token is not None:
        user = await _authenticate_api_token(db, api_token)
        request.state.auth_via = 'api_token'
        set_request_actor(actor_for(user, 'api_token'))
        return user

    # Cookie first, Authorization header as the fallback (used by
    # browser-use wrapper scripts for auto-start).
    token = cookie_token or bearer_token

    auth_on = await is_auth_required(db)

    # A valid token authenticates as that user.
    if token:
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
            user = await db.get(User, payload.get('sub', ''))
            if user and user.is_active:
                via = 'session' if cookie_token else 'service'
                request.state.auth_via = via
                set_request_actor(actor_for(user, via))
                return user
        except jwt.PyJWTError:
            user = None
        # Token present but invalid/expired/user-gone. Only reject when
        # auth is required; otherwise fall through to the default admin.
        # (JWT_SECRET is per-install, so a stale cookie from a prior
        # install would otherwise force login on a no-auth server.)
        if auth_on:
            raise HTTPException(status_code=401, detail='Invalid token')

    # No usable token — when auth is disabled, act as the default admin.
    if not auth_on:
        user = await db.get(User, DEFAULT_USER_ID)
        if user:
            request.state.auth_via = 'no_auth'
            set_request_actor(actor_for(user, 'no_auth'))
            return user

    raise HTTPException(status_code=401, detail='Not authenticated')


async def require_admin(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    if user.role != 'admin':
        raise HTTPException(status_code=403, detail='Admin access required')
    return user
