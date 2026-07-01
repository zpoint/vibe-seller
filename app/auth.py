"""Authentication utilities — password hashing, JWT tokens, FastAPI deps."""

from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, Request
import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import DEFAULT_USER_ID, JWT_SECRET
from app.database import get_db
from app.models.app_settings import AppSettings
from app.models.user import User

ALGORITHM = 'HS256'
TOKEN_EXPIRE_DAYS = 7
COOKIE_NAME = 'auth_token'


def create_token(user_id: str, role: str) -> str:
    expire = datetime.now(UTC) + timedelta(days=TOKEN_EXPIRE_DAYS)
    return jwt.encode(
        {'sub': user_id, 'role': role, 'exp': expire},
        JWT_SECRET,
        algorithm=ALGORITHM,
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


async def get_current_user(
    request: Request, db: AsyncSession = Depends(get_db)
):
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        # Fall back to Authorization: Bearer header (used by
        # browser-use wrapper scripts for auto-start).
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            token = auth_header[7:]

    auth_on = await is_auth_required(db)

    # A valid token authenticates as that user.
    if token:
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
            user = await db.get(User, payload.get('sub', ''))
            if user and user.is_active:
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
            return user

    raise HTTPException(status_code=401, detail='Not authenticated')


async def require_admin(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    if user.role != 'admin':
        raise HTTPException(status_code=403, detail='Admin access required')
    return user
