from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import telemetry
from app.auth import require_admin
from app.database import get_db
from app.models.email_account import EmailAccount
from app.models.event import Event
from app.models.event_activity import EventActivity
from app.models.schedule import Schedule
from app.models.task import Task
from app.models.user import User
from app.password import hash_password
from app.schemas.user import UserCreate, UserResponse, UserUpdate
from app.telemetry_events import TelemetryEvent

router = APIRouter(prefix='/api/users', tags=['users'])


async def _check_identifier_unique(
    db: AsyncSession, value: str, exclude_user_id: str | None = None
):
    """Ensure value is not taken as username or email by another."""
    q = select(User).where(or_(User.username == value, User.email == value))
    if exclude_user_id:
        q = q.where(User.id != exclude_user_id)
    result = await db.execute(q)
    return result.scalar_one_or_none() is None


@router.get('', response_model=list[UserResponse])
async def list_users(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    result = await db.execute(select(User).order_by(User.created_at))
    return result.scalars().all()


@router.post('', response_model=UserResponse)
async def create_user(
    body: UserCreate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    # Cross-column uniqueness: username != any existing email/username
    if not await _check_identifier_unique(db, body.username):
        raise HTTPException(status_code=400, detail='Username already in use')
    if body.email and not await _check_identifier_unique(db, body.email):
        raise HTTPException(status_code=400, detail='Email already in use')
    if body.role not in ('admin', 'member'):
        raise HTTPException(status_code=400, detail='Invalid role')
    user = User(
        username=body.username,
        email=body.email,
        password_hash=hash_password(body.password),
        role=body.role,
        default_profile_id=_admin.default_profile_id,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=400,
            detail='Username or email already in use',
        )
    await db.refresh(user)
    telemetry.send(TelemetryEvent.ADMIN_USER_CREATED, {'role': user.role})
    return user


@router.put('/{user_id}', response_model=UserResponse)
async def update_user(
    user_id: str,
    body: UserUpdate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail='User not found')
    if user.role == 'ai_bot':
        raise HTTPException(
            status_code=400,
            detail='Cannot modify AI bot user',
        )
    if body.username is not None:
        if not await _check_identifier_unique(db, body.username, user.id):
            raise HTTPException(
                status_code=400, detail='Username already in use'
            )
        user.username = body.username
    if 'email' in body.model_fields_set:
        if body.email is not None:
            if not await _check_identifier_unique(db, body.email, user.id):
                raise HTTPException(
                    status_code=400,
                    detail='Email already in use',
                )
        user.email = body.email
    if body.password is not None:
        user.password_hash = hash_password(body.password)
    if body.role is not None:
        if body.role not in ('admin', 'member'):
            raise HTTPException(status_code=400, detail='Invalid role')
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
    user.updated_at = datetime.now(UTC).isoformat()
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=400,
            detail='Username or email already in use',
        )
    await db.refresh(user)
    return user


@router.delete('/{user_id}')
async def delete_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail='User not found')
    if user.role == 'ai_bot':
        raise HTTPException(
            status_code=400,
            detail='Cannot delete AI bot user',
        )
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail='Cannot delete yourself')
    # Nullify FK references before hard-deleting
    await db.execute(
        update(Task)
        .where(Task.created_by == user_id)
        .values(created_by=admin.id)
    )
    await db.execute(
        update(Schedule)
        .where(Schedule.created_by == user_id)
        .values(created_by=admin.id)
    )
    await db.execute(
        update(EmailAccount)
        .where(EmailAccount.created_by == user_id)
        .values(created_by=admin.id)
    )
    await db.execute(
        update(Event).where(Event.created_by == user_id).values(created_by=None)
    )
    await db.execute(
        update(EventActivity)
        .where(EventActivity.user_id == user_id)
        .values(user_id=None)
    )
    deleted_role = user.role
    await db.delete(user)
    await db.commit()
    telemetry.send(TelemetryEvent.ADMIN_USER_DELETED, {'role': deleted_role})
    return {'ok': True}
