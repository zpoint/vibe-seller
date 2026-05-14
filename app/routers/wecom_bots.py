"""CRUD + test endpoints for WeChat Work (企业微信) group bots.

Security note: the webhook URL carries a secret key (`?key=...`).
List responses mask the URL so it doesn't appear on a shared
screen; the full URL is only returned by the single-bot GET
(used by the edit form) and by create/update responses (the
caller just supplied it).
"""

from datetime import UTC, datetime
import logging
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.user import User
from app.models.wecom_bot import WeComBot
from app.notifiers.wecom import send_webhook
from app.schemas.wecom_bot import (
    WeComBotCreate,
    WeComBotResponse,
    WeComBotSendRequest,
    WeComBotSummary,
    WeComBotTestRequest,
    WeComBotUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=['wecom-bots'])

DEFAULT_TEST_MESSAGE = 'Vibe Seller: WeCom bot connection test ✅'


def _mask_webhook_url(url: str) -> str:
    """Return host + last 4 chars of the `key` query param.

    Example: `https://qyapi.weixin.qq.com/...?key=****abcd`.
    Falls back to '***' for malformed URLs so we never echo the raw
    string back to the client.
    """
    try:
        parts = urlsplit(url)
        host = parts.netloc or '***'
        key_tail = ''
        for piece in parts.query.split('&'):
            if piece.startswith('key='):
                key = piece[4:]
                key_tail = key[-4:] if len(key) > 4 else ''
                break
        suffix = f'?key=****{key_tail}' if key_tail else ''
        return f'https://{host}/...{suffix}'
    except Exception:
        return '***'


def _summary(bot: WeComBot) -> WeComBotSummary:
    return WeComBotSummary(
        id=bot.id,
        name=bot.name,
        webhook_url_masked=_mask_webhook_url(bot.webhook_url),
        created_at=bot.created_at,
        updated_at=bot.updated_at,
    )


@router.get(
    '/api/wecom-bots',
    response_model=list[WeComBotSummary],
)
async def list_wecom_bots(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """List bots with the webhook URL masked (secret protection)."""
    result = await db.execute(
        select(WeComBot).order_by(WeComBot.created_at.desc())
    )
    return [_summary(b) for b in result.scalars().all()]


@router.get(
    '/api/wecom-bots/{bot_id}',
    response_model=WeComBotResponse,
)
async def get_wecom_bot(
    bot_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Return a single bot with the full webhook URL (for edit)."""
    bot = await db.get(WeComBot, bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail='Bot not found')
    return bot


@router.post('/api/wecom-bots', response_model=WeComBotResponse)
async def create_wecom_bot(
    data: WeComBotCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Create a new WeCom group bot config."""
    name = data.name.strip()
    url = data.webhook_url.strip()
    if not name:
        raise HTTPException(status_code=400, detail='Name is required')
    if not url:
        raise HTTPException(status_code=400, detail='Webhook URL is required')

    bot = WeComBot(name=name, webhook_url=url, created_by=user.id)
    db.add(bot)
    await db.commit()
    await db.refresh(bot)
    return bot


@router.put(
    '/api/wecom-bots/{bot_id}',
    response_model=WeComBotResponse,
)
async def update_wecom_bot(
    bot_id: str,
    data: WeComBotUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Update bot fields. Missing fields are left untouched; blank
    or None values for required fields are rejected with a 400.
    """
    bot = await db.get(WeComBot, bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail='Bot not found')

    update = data.model_dump(exclude_unset=True)
    for field, value in update.items():
        if value is None:
            # Explicit null for a required column → reject rather
            # than letting it hit the NOT NULL constraint.
            raise HTTPException(
                status_code=400,
                detail=f'{field} cannot be null',
            )
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise HTTPException(
                    status_code=400,
                    detail=f'{field} cannot be blank',
                )
        setattr(bot, field, value)
    bot.updated_at = datetime.now(UTC).isoformat()
    await db.commit()
    await db.refresh(bot)
    return bot


@router.delete('/api/wecom-bots/{bot_id}')
async def delete_wecom_bot(
    bot_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Delete a WeCom bot config."""
    bot = await db.get(WeComBot, bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail='Bot not found')
    await db.delete(bot)
    await db.commit()
    return {'ok': True}


@router.post('/api/wecom-bots/{bot_id}/test')
async def test_wecom_bot(
    bot_id: str,
    data: WeComBotTestRequest | None = None,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Send a test message to verify the webhook works."""
    bot = await db.get(WeComBot, bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail='Bot not found')

    raw = data.content if data else None
    content = (raw.strip() if raw else '') or DEFAULT_TEST_MESSAGE
    ok, err = await send_webhook(bot.webhook_url, content)
    return {'ok': ok, 'message': err or 'Message sent'}


@router.post('/api/wecom-bots/{bot_id}/send')
async def send_wecom_message(
    bot_id: str,
    data: WeComBotSendRequest,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Post a real message (text or markdown) through the bot."""
    bot = await db.get(WeComBot, bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail='Bot not found')

    content = (data.content or '').strip()
    if not content:
        raise HTTPException(status_code=400, detail='content cannot be blank')
    if data.msgtype not in ('text', 'markdown'):
        raise HTTPException(
            status_code=400,
            detail="msgtype must be 'text' or 'markdown'",
        )

    ok, err = await send_webhook(bot.webhook_url, content, msgtype=data.msgtype)
    return {'ok': ok, 'message': err or 'Message sent'}
