"""
Event API routes — manage business events extracted from channel messages.

Events are deadlines, campaigns, meetings etc. parsed from email/WeChat messages.
They can be synced to external backends (Dida365, Google Calendar).
Supports multi-day tracking with activity timeline.
"""

from datetime import UTC, datetime
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.events.bus import event_bus
from app.events_system.syncer import (
    EVENT_BACKEND_REGISTRY,
    event_syncer,
    load_backend_config,
    save_backend_config,
)
from app.models.event import Event
from app.models.event_activity import EventActivity
from app.models.user import User
from app.schemas.event import (
    BackendConfigRequest,
    EventActivityCreate,
    EventActivityResponse,
    EventCreate,
    EventResponse,
    EventStatusChange,
    EventUpdate,
)

router = APIRouter(prefix='/api/events', tags=['events'])

VALID_STATUSES = {
    'draft',
    'open',
    'in_progress',
    'waiting',
    'resolved',
    'closed',
    'dismissed',
}
VALID_TRANSITIONS = {
    'draft': {'open', 'dismissed'},
    'open': {'in_progress', 'waiting', 'resolved', 'closed', 'dismissed'},
    'in_progress': {'waiting', 'resolved', 'closed'},
    'waiting': {'in_progress', 'resolved', 'closed'},
    'resolved': {'closed', 'open'},
    'closed': {'open'},
    'dismissed': {'open'},
}


async def _log_activity(
    db: AsyncSession,
    event_id: str,
    user_id: str | None,
    actor_type: str,
    action: str,
    content: str,
    extra_data: dict | None = None,
):
    activity = EventActivity(
        event_id=event_id,
        user_id=user_id,
        actor_type=actor_type,
        action=action,
        content=content,
        extra_data=json.dumps(extra_data) if extra_data else None,
    )
    db.add(activity)
    await db.flush()
    await db.refresh(activity)
    await event_bus.emit(
        'event_activity',
        {
            'event_id': event_id,
            'activity_id': activity.id,
            'actor_type': actor_type,
            'action': action,
            'content': content,
        },
    )
    return activity


@router.get('', response_model=list[EventResponse])
async def list_events(
    status: str | None = None,
    store_id: str | None = None,
    category: str | None = None,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """List events, optionally filtered by status, store_id, category."""
    q = select(Event).order_by(Event.created_at.desc())
    if status:
        statuses = [s.strip() for s in status.split(',') if s.strip()]
        if len(statuses) == 1:
            q = q.where(Event.status == statuses[0])
        else:
            q = q.where(Event.status.in_(statuses))
    if store_id:
        q = q.where(Event.store_id == store_id)
    if category == 'user':
        q = q.where(Event.platform != 'system')
    elif category == 'system':
        q = q.where(Event.platform == 'system')
    result = await db.execute(q)
    return result.scalars().all()


@router.post('', response_model=EventResponse)
async def create_event(
    body: EventCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manually create a new event."""
    event = Event(
        title=body.title,
        description=body.description,
        event_date=body.event_date,
        deadline=body.deadline,
        platform=body.platform,
        store_id=body.store_id,
        case_id=body.case_id,
        assignees=body.assignees,
        priority=body.priority,
        sync_backend=body.sync_backend,
        created_by=current_user.id,
        status='open',
    )
    db.add(event)
    await db.flush()
    await db.refresh(event)

    await _log_activity(
        db,
        event.id,
        current_user.id,
        'user',
        'created',
        f'Event created: {event.title}',
    )

    await db.commit()
    await db.refresh(event)

    await event_bus.emit(
        'event_created',
        {
            'event_id': event.id,
            'title': event.title,
            'status': event.status,
        },
    )
    return event


@router.get('/backends')
async def list_backends(_user: User = Depends(get_current_user)):
    """List available sync backends and their config status."""
    backends = []
    for name in EVENT_BACKEND_REGISTRY:
        config = load_backend_config(name)
        backends.append({'name': name, 'configured': bool(config)})
    return backends


@router.post('/backends/configure')
async def configure_backend(
    body: BackendConfigRequest, _user: User = Depends(get_current_user)
):
    """Configure a sync backend with API keys/credentials."""
    if body.backend not in EVENT_BACKEND_REGISTRY:
        raise HTTPException(
            status_code=400, detail=f'Unknown backend: {body.backend}'
        )
    save_backend_config(body.backend, body.config)
    return {'ok': True, 'backend': body.backend}


@router.get('/{event_id}', response_model=EventResponse)
async def get_event(
    event_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail='Event not found')
    return event


@router.put('/{event_id}', response_model=EventResponse)
async def update_event(
    event_id: str,
    body: EventUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Edit an event's fields."""
    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail='Event not found')

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(event, field, value)
    event.updated_at = datetime.now(UTC).isoformat()
    await db.commit()
    await db.refresh(event)
    return event


@router.post('/{event_id}/status', response_model=EventResponse)
async def change_event_status(
    event_id: str,
    body: EventStatusChange,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Change event status with validation of allowed transitions."""
    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail='Event not found')

    new_status = body.status
    if new_status not in VALID_STATUSES:
        raise HTTPException(
            status_code=400, detail=f'Invalid status: {new_status}'
        )

    allowed = VALID_TRANSITIONS.get(event.status, set())
    if new_status not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f'Cannot transition from {event.status} to {new_status}',
        )

    old_status = event.status
    event.status = new_status
    event.updated_at = datetime.now(UTC).isoformat()

    await _log_activity(
        db,
        event.id,
        current_user.id,
        'user',
        'status_changed',
        f'Status changed: {old_status} -> {new_status}',
        {'old_status': old_status, 'new_status': new_status},
    )

    await db.commit()
    await db.refresh(event)

    await event_bus.emit(
        'event_updated',
        {
            'event_id': event.id,
            'status': event.status,
            'old_status': old_status,
        },
    )
    return event


@router.post('/{event_id}/confirm', response_model=EventResponse)
async def confirm_event(
    event_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Confirm a draft event (sets to 'open') and trigger sync if configured."""
    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail='Event not found')
    if event.status not in ('draft',):
        raise HTTPException(
            status_code=400,
            detail=f'Cannot confirm event in status {event.status}',
        )

    old_status = event.status
    event.status = 'open'
    event.updated_at = datetime.now(UTC).isoformat()

    await _log_activity(
        db,
        event.id,
        current_user.id,
        'user',
        'status_changed',
        f'Confirmed: {old_status} -> open',
        {'old_status': old_status, 'new_status': 'open'},
    )

    # Auto-sync if backend is set
    if event.sync_backend:
        try:
            sync_id = await event_syncer.sync_event(
                event.sync_backend,
                event.title,
                event.description,
                event.event_date,
                event.deadline,
            )
            event.sync_id = sync_id
            event.sync_error = None
            await _log_activity(
                db,
                event.id,
                None,
                'system',
                'synced',
                f'Synced to {event.sync_backend}',
            )
        except Exception as e:
            event.sync_error = str(e)

    await db.commit()
    await db.refresh(event)

    await event_bus.emit(
        'event_updated',
        {
            'event_id': event.id,
            'status': event.status,
        },
    )
    return event


@router.get(
    '/{event_id}/activities', response_model=list[EventActivityResponse]
)
async def list_activities(
    event_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """List activity timeline for an event."""
    result = await db.execute(
        select(EventActivity)
        .where(EventActivity.event_id == event_id)
        .order_by(EventActivity.created_at)
    )
    return result.scalars().all()


@router.post('/{event_id}/activities', response_model=EventActivityResponse)
async def add_activity(
    event_id: str,
    body: EventActivityCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Add a note or context to event timeline."""
    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail='Event not found')

    actor_type = 'ai' if current_user.role == 'ai_bot' else 'user'
    activity = await _log_activity(
        db,
        event_id,
        current_user.id,
        actor_type,
        body.action,
        body.content,
    )

    event.updated_at = datetime.now(UTC).isoformat()
    await db.commit()
    await db.refresh(activity)
    return activity


@router.post('/{event_id}/dismiss', response_model=EventResponse)
async def dismiss_event(
    event_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Dismiss a draft event."""
    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail='Event not found')

    old_status = event.status
    event.status = 'dismissed'
    event.updated_at = datetime.now(UTC).isoformat()

    await _log_activity(
        db,
        event.id,
        current_user.id,
        'user',
        'status_changed',
        f'Dismissed: {old_status} -> dismissed',
        {'old_status': old_status, 'new_status': 'dismissed'},
    )

    await db.commit()
    await db.refresh(event)
    return event


@router.post('/{event_id}/sync', response_model=EventResponse)
async def sync_event(
    event_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Sync or re-sync an event to its configured backend."""
    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail='Event not found')
    if not event.sync_backend:
        raise HTTPException(
            status_code=400, detail='No sync backend configured for this event'
        )

    try:
        if event.sync_id:
            await event_syncer.update_event(
                event.sync_backend,
                event.sync_id,
                event.title,
                event.description,
                event.event_date,
                event.deadline,
            )
        else:
            sync_id = await event_syncer.sync_event(
                event.sync_backend,
                event.title,
                event.description,
                event.event_date,
                event.deadline,
            )
            event.sync_id = sync_id
        event.sync_error = None

        await _log_activity(
            db,
            event.id,
            None,
            'system',
            'synced',
            f'Synced to {event.sync_backend}',
        )
    except Exception as e:
        event.sync_error = str(e)

    event.updated_at = datetime.now(UTC).isoformat()
    await db.commit()
    await db.refresh(event)
    return event


@router.delete('/{event_id}')
async def delete_event(
    event_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Delete an event. If synced, also deletes from external backend."""
    event = await db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail='Event not found')

    if event.sync_id and event.sync_backend:
        try:
            await event_syncer.delete_event(event.sync_backend, event.sync_id)
        except Exception as e:
            logging.getLogger(__name__).warning(
                f'Failed to delete from backend: {e}'
            )

    # Delete activities first
    activities = await db.execute(
        select(EventActivity).where(EventActivity.event_id == event_id)
    )
    for a in activities.scalars().all():
        await db.delete(a)

    await db.delete(event)
    await db.commit()
    return {'ok': True}
