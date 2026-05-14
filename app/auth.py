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

    # If a token is present, authenticate via JWT directly
    if token:
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
            user_id: str = payload.get('sub', '')
        except jwt.PyJWTError:
            raise HTTPException(status_code=401, detail='Invalid token')
        user = await db.get(User, user_id)
        if not user or not user.is_active:
            raise HTTPException(
                status_code=401,
                detail='User not found or inactive',
            )
        return user

    # No token — when auth is disabled, return default admin
    if not await is_auth_required(db):
        user = await db.get(User, DEFAULT_USER_ID)
        if user:
            return user

    raise HTTPException(status_code=401, detail='Not authenticated')


async def require_admin(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    if user.role != 'admin':
        raise HTTPException(status_code=403, detail='Admin access required')
    return user
