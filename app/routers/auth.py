from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import telemetry
from app.api_tokens import create_token_row
from app.auth import (
    COOKIE_NAME,
    USER_SESSION_EXPIRE,
    create_token,
    get_current_user,
    is_auth_required,
    set_session_cookie,
)
from app.config import DEFAULT_USER_ID
from app.database import get_db
from app.models.api_token import ApiToken
from app.models.app_settings import AppSettings
from app.models.user import User
from app.password import hash_password, verify_password
from app.schemas.api_token import (
    ApiTokenCreate,
    ApiTokenCreatedResponse,
    ApiTokenResponse,
)
from app.schemas.user import (
    LoginRequest,
    PasswordChange,
    ProfileUpdate,
    UserResponse,
)
from app.telemetry_events import TelemetryEvent

router = APIRouter(prefix='/api/auth', tags=['auth'])


@router.get('/status')
async def auth_status(db: AsyncSession = Depends(get_db)):
    """Public endpoint — no auth required."""
    return {'auth_required': await is_auth_required(db)}


@router.post('/login')
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    # Disambiguate: @ means email lookup, otherwise username
    if '@' in body.identifier:
        result = await db.execute(
            select(User).where(User.email == body.identifier)
        )
    else:
        result = await db.execute(
            select(User).where(User.username == body.identifier.lower())
        )
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail='Invalid credentials')
    if user.role == 'ai_bot':
        raise HTTPException(status_code=401, detail='Invalid credentials')
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail='Invalid credentials')
    token = create_token(user.id, user.role, expires_delta=USER_SESSION_EXPIRE)
    response = JSONResponse(
        content={
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'role': user.role,
            'default_profile_id': user.default_profile_id,
            'debug_mode': user.debug_mode,
            'plan_mode_default': user.plan_mode_default,
            'sync_profile_to_schedules': user.sync_profile_to_schedules,
        }
    )
    set_session_cookie(response, token)
    return response


@router.post('/refresh')
async def refresh_session(
    current_user: User = Depends(get_current_user),
):
    """Roll the session cookie forward by another full window.

    The frontend keepalive calls this on real user activity so an
    actively-used session never expires out from under the user; the
    24h window only elapses on genuine idleness. When auth is required
    and the cookie has already lapsed, ``get_current_user`` raises 401
    here — which the shared API client turns into an ``auth:expired``
    redirect to the login page (no hung screen).

    An API token cannot refresh: that would trade a revocable ``vst_``
    credential for a session JWT that outlives the token's revocation
    — refused by ``ApiTokenScopeMiddleware`` (``app/api_token_scope.py``),
    the one place every credential-issuing route is guarded.
    """
    token = create_token(
        current_user.id, current_user.role, expires_delta=USER_SESSION_EXPIRE
    )
    response = JSONResponse(content={'ok': True})
    set_session_cookie(response, token)
    return response


@router.post('/logout')
async def logout():
    response = JSONResponse(content={'ok': True})
    response.delete_cookie(key=COOKIE_NAME)
    return response


@router.get('/me', response_model=UserResponse)
async def me(
    current_user: User = Depends(get_current_user),
):
    return current_user


@router.patch('/me/debug-mode')
async def toggle_debug_mode(
    body: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Toggle debug_mode for the current user."""
    debug_mode = bool(body.get('debug_mode', False))
    current_user.debug_mode = debug_mode
    db.add(current_user)
    await db.commit()
    telemetry.send(
        TelemetryEvent.USER_PREF_CHANGED,
        {'key': 'debug_mode', 'to_value': debug_mode},
    )
    return {'ok': True, 'debug_mode': debug_mode}


@router.patch('/me/password')
async def change_password(
    body: PasswordChange,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Change own password. Skip current_password when auth off."""
    auth_on = await is_auth_required(db)
    if auth_on and body.current_password:
        if not verify_password(
            body.current_password, current_user.password_hash
        ):
            raise HTTPException(
                status_code=400,
                detail='Current password is incorrect',
            )
    elif auth_on and not body.current_password:
        raise HTTPException(
            status_code=400,
            detail='Current password is required',
        )

    current_user.password_hash = hash_password(body.new_password)
    current_user.updated_at = datetime.now(UTC).isoformat()

    # Mark admin credentials as user-set
    if current_user.id == DEFAULT_USER_ID:
        existing = await db.get(AppSettings, 'admin_credentials_set')
        if not existing:
            db.add(AppSettings(key='admin_credentials_set', value='true'))

    await db.commit()
    return {'ok': True}


async def _check_identifier_unique(
    db: AsyncSession, value: str, exclude_user_id: str | None = None
):
    """Ensure value is not taken as username or email by another user."""
    q = select(User).where(or_(User.username == value, User.email == value))
    if exclude_user_id:
        q = q.where(User.id != exclude_user_id)
    result = await db.execute(q)
    return result.scalar_one_or_none() is None


@router.patch('/me/profile')
async def update_profile(
    body: ProfileUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update own profile."""
    changed = False

    if body.username is not None and body.username != current_user.username:
        if not await _check_identifier_unique(
            db, body.username, current_user.id
        ):
            raise HTTPException(
                status_code=400, detail='Username already in use'
            )
        current_user.username = body.username
        changed = True

    if 'email' in body.model_fields_set and body.email != current_user.email:
        if current_user.role != 'admin':
            raise HTTPException(
                status_code=403,
                detail='Only admin can change email',
            )
        if body.email is not None:
            if not await _check_identifier_unique(
                db, body.email, current_user.id
            ):
                raise HTTPException(
                    status_code=400, detail='Email already in use'
                )
        current_user.email = body.email
        changed = True

    if (
        body.plan_mode_default is not None
        and body.plan_mode_default != current_user.plan_mode_default
    ):
        current_user.plan_mode_default = body.plan_mode_default
        telemetry.send(
            TelemetryEvent.USER_PREF_CHANGED,
            {
                'key': 'plan_mode_default',
                'to_value': bool(body.plan_mode_default),
            },
        )
        changed = True

    if (
        body.sync_profile_to_schedules is not None
        and body.sync_profile_to_schedules
        != current_user.sync_profile_to_schedules
    ):
        current_user.sync_profile_to_schedules = body.sync_profile_to_schedules
        telemetry.send(
            TelemetryEvent.USER_PREF_CHANGED,
            {
                'key': 'sync_profile_to_schedules',
                'to_value': bool(body.sync_profile_to_schedules),
            },
        )
        changed = True

    if changed:
        current_user.updated_at = datetime.now(UTC).isoformat()
        # Mark admin credentials as user-set
        if current_user.id == DEFAULT_USER_ID:
            existing = await db.get(AppSettings, 'admin_credentials_set')
            if not existing:
                db.add(
                    AppSettings(
                        key='admin_credentials_set',
                        value='true',
                    )
                )
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(
                status_code=400,
                detail='Username or email already in use',
            )

    return {'ok': True}


# ── Personal API tokens ──────────────────────────────
#
# A user manages their OWN tokens. A token acts as its owner, with the
# owner's role — there are no scopes and no separate permission model.


@router.get('/tokens', response_model=list[ApiTokenResponse])
async def list_api_tokens(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List the caller's live (non-revoked) API tokens.

    The secret is never part of this response — only the prefix that
    was stored at creation time.
    """
    result = await db.execute(
        select(ApiToken)
        .where(ApiToken.user_id == current_user.id)
        .where(ApiToken.revoked_at.is_(None))
        .order_by(ApiToken.created_at.desc())
    )
    return list(result.scalars().all())


@router.post('/tokens', response_model=ApiTokenCreatedResponse)
async def create_api_token(
    body: ApiTokenCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mint an API token. The plaintext is returned ONCE, here."""
    if current_user.role == 'ai_bot':
        raise HTTPException(
            status_code=403,
            detail='AI bot tokens cannot create API tokens',
        )
    # A leaked token must not be able to mint its own replacement: a
    # ``vst_`` credential can list and revoke tokens (revoking itself
    # is harmless — that is what its owner wants) but never mint one,
    # so cutting a leaked token's access stays cut — refused by
    # ``ApiTokenScopeMiddleware`` with the user-management routes.
    row, plaintext = await create_token_row(
        db, current_user.id, body.name, body.expires_in_days
    )
    return ApiTokenCreatedResponse(
        id=row.id,
        name=row.name,
        prefix=row.prefix,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        expires_at=row.expires_at,
        token=plaintext,
    )


@router.delete('/tokens/{token_id}')
async def revoke_api_token(
    token_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Revoke one of the caller's own tokens.

    Revoking is a soft delete: the row stays, so a client still holding
    the plaintext gets a 401 and the token can never be resurrected.
    """
    result = await db.execute(
        select(ApiToken).where(
            ApiToken.id == token_id,
            ApiToken.user_id == current_user.id,
            ApiToken.revoked_at.is_(None),
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        # Another user's token, an unknown id and an already-revoked id
        # are all "not found" — never "forbidden", which would confirm
        # that the id exists.
        raise HTTPException(status_code=404, detail='Token not found')
    row.revoked_at = datetime.now(UTC).isoformat()
    await db.commit()
    return {'ok': True}
