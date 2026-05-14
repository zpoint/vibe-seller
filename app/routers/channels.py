import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.channels.base import (
    CHANNEL_REGISTRY,
    BaseChannel,
    ReadWriteChannel,
    get_channel,
)
from app.database import get_db
from app.events.bus import event_bus
from app.events_system.extractor import event_extractor
from app.events_system.syncer import (
    EVENT_BACKEND_REGISTRY,
    event_syncer,
    load_backend_config,
)
from app.models.event import Event
from app.models.event_activity import EventActivity
from app.models.user import User
from app.workspace.manager import VIBE_SELLER_DIR

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/api/channels', tags=['channels'])

# Persistent config file
CHANNELS_CONFIG_FILE = VIBE_SELLER_DIR / 'config' / 'channels.json'

# Active channel instances
_active_channels: dict[str, BaseChannel] = {}
_channel_auto_create_events: dict[str, bool] = {}


def _load_saved_configs() -> dict:
    """Load channel configs from disk."""
    if CHANNELS_CONFIG_FILE.exists():
        try:
            return json.loads(CHANNELS_CONFIG_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {}


def _save_configs(configs: dict):
    """Save channel configs to disk."""
    CHANNELS_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHANNELS_CONFIG_FILE.write_text(
        json.dumps(configs, indent=2, ensure_ascii=False), encoding='utf-8'
    )


class ChannelConfigRequest(BaseModel):
    channel_type: str
    config: dict
    channel_id: str = ''  # optional custom ID
    auto_create_events: bool = False  # auto-create events from messages


class SendMessageRequest(BaseModel):
    content: str
    recipient: str = ''


@router.get('/types')
async def list_channel_types(_user: User = Depends(get_current_user)):
    """List available channel types and their capabilities."""
    result = []
    for ctype, cls in CHANNEL_REGISTRY.items():
        result.append({
            'type': ctype,
            'read_write': issubclass(cls, ReadWriteChannel),
        })
    return result


@router.post('/configure')
async def configure_channel(
    body: ChannelConfigRequest, _user: User = Depends(get_current_user)
):
    """Configure and activate a channel instance."""
    channel_id = body.channel_id or body.channel_type
    channel = get_channel(body.channel_type)
    await channel.configure(body.config)
    _active_channels[channel_id] = channel
    _channel_auto_create_events[channel_id] = body.auto_create_events

    # Persist config to disk
    saved = _load_saved_configs()
    saved[channel_id] = {
        'channel_type': body.channel_type,
        'config': body.config,
        'auto_create_events': body.auto_create_events,
    }
    _save_configs(saved)

    return {
        'channel_id': channel_id,
        'status': 'configured',
        'auto_create_events': body.auto_create_events,
    }


@router.get('/configs')
async def get_channel_configs(_user: User = Depends(get_current_user)):
    """Get all saved channel configurations."""
    return _load_saved_configs()


@router.get('/active')
async def list_active_channels(_user: User = Depends(get_current_user)):
    """List active channel instances."""
    return [
        {
            'channel_id': cid,
            'channel_type': ch.channel_type,
            'read_write': isinstance(ch, ReadWriteChannel),
        }
        for cid, ch in _active_channels.items()
    ]


@router.post('/{channel_id}/poll')
async def poll_channel(
    channel_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Poll a channel for new messages and extract events."""
    channel = _active_channels.get(channel_id)
    if not channel:
        raise HTTPException(
            status_code=404, detail=f'Channel not found: {channel_id}'
        )
    messages = await channel.poll()

    auto_create = _channel_auto_create_events.get(channel_id, False)

    # Extract events from each message
    events_created = []
    for m in messages:
        text = f'{m.subject}\n{m.content}' if m.subject else m.content
        extracted = await event_extractor.extract_events(
            text, channel.channel_type
        )

        for ev in extracted:
            status = 'draft'
            sync_id = None
            sync_error = None

            # Determine initial sync backend (first configured one)
            sync_backend = None
            for backend_name in EVENT_BACKEND_REGISTRY:
                if load_backend_config(backend_name):
                    sync_backend = backend_name
                    break

            if auto_create and sync_backend:
                try:
                    sync_id = await event_syncer.sync_event(
                        sync_backend,
                        ev.get('title', ''),
                        ev.get('description'),
                        ev.get('event_date'),
                        ev.get('deadline'),
                    )
                    status = 'synced'
                except Exception as e:
                    sync_error = str(e)
                    status = 'confirmed'

            event = Event(
                channel_message_id=m.message_id or None,
                channel_type=channel.channel_type,
                title=ev.get('title', 'Untitled Event'),
                description=ev.get('description'),
                event_date=ev.get('event_date'),
                deadline=ev.get('deadline'),
                platform=ev.get('platform'),
                source_text=text[:2000],
                status=status,
                sync_backend=sync_backend,
                sync_id=sync_id,
                sync_error=sync_error,
            )
            db.add(event)
            events_created.append(event)

    if events_created:
        await db.flush()
        for event in events_created:
            await db.refresh(event)
            db.add(
                EventActivity(
                    event_id=event.id,
                    user_id=None,
                    actor_type='channel',
                    action='created',
                    content=f'Extracted from {channel.channel_type} channel',
                )
            )
        await db.commit()
        for event in events_created:
            await event_bus.emit(
                'event_created',
                {
                    'event_id': event.id,
                    'title': event.title,
                    'status': event.status,
                },
            )

    return {
        'messages': [
            {
                'sender': m.sender,
                'content': m.content,
                'subject': m.subject,
                'message_id': m.message_id,
            }
            for m in messages
        ],
        'events_created': len(events_created),
    }


@router.post('/{channel_id}/send')
async def send_message(
    channel_id: str,
    body: SendMessageRequest,
    _user: User = Depends(get_current_user),
):
    """Send a message through a read-write channel."""
    channel = _active_channels.get(channel_id)
    if not channel:
        raise HTTPException(
            status_code=404, detail=f'Channel not found: {channel_id}'
        )
    if not isinstance(channel, ReadWriteChannel):
        raise HTTPException(
            status_code=400, detail=f'Channel {channel_id} is read-only'
        )
    success = await channel.send(body.content, body.recipient)
    return {'ok': success}


@router.delete('/{channel_id}')
async def remove_channel(
    channel_id: str, _user: User = Depends(get_current_user)
):
    """Remove and close an active channel."""
    channel = _active_channels.pop(channel_id, None)
    if not channel:
        raise HTTPException(
            status_code=404, detail=f'Channel not found: {channel_id}'
        )
    await channel.close()
    return {'ok': True}
