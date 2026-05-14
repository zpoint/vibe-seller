from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import telemetry
from app.auth import (
    COOKIE_NAME,
    TOKEN_EXPIRE_DAYS,
    create_token,
    get_current_user,
    is_auth_required,
)
from app.config import DEFAULT_USER_ID
from app.database import get_db
from app.models.app_settings import AppSettings
from app.models.user import User
from app.password import hash_password, verify_password
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
    token = create_token(user.id, user.role)
    response = JSONResponse(
        content={
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'role': user.role,
            'default_profile_id': user.default_profile_id,
            'debug_mode': user.debug_mode,
            'plan_mode_default': user.plan_mode_default,
        }
    )
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite='lax',
        max_age=TOKEN_EXPIRE_DAYS * 86400,
    )
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
