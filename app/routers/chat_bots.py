"""CRUD + test endpoints for chat bots (group robots).

Supported platforms: WeCom / WeChat Work (企业微信), DingTalk (钉钉),
Feishu / Lark (飞书), Slack.

Security note: the webhook URL carries a secret (WeCom ``?key=``,
DingTalk ``?access_token=``, Feishu/Slack a path token) and the
optional signing secret is a secret too. List responses mask the URL and
report only ``has_secret``; the raw values are returned by the
single-bot GET (used by the edit form) and by create/update responses
(the caller just supplied them).

The platform is never taken from the client — it is derived from the
webhook URL host by :func:`app.notifiers.base.detect_platform`, so a
stored bot can never disagree with its own URL and a secret can never
be posted to a host the user did not choose.
"""

from datetime import UTC, datetime
import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.config import DOWNLOADS_DIR
from app.database import get_db
from app.models.chat_bot import ChatBot
from app.models.user import User
from app.notifiers import base
from app.notifiers.base import PLATFORMS, Platform
from app.notifiers.wecom import send_file_webhook
from app.schemas.chat_bot import (
    ChatBotCreate,
    ChatBotResponse,
    ChatBotSendFileRequest,
    ChatBotSendRequest,
    ChatBotSummary,
    ChatBotTestRequest,
    ChatBotUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=['chat-bots'])

DEFAULT_TEST_MESSAGE = 'Vibe Seller: chat bot connection test ✅'

UNSUPPORTED_URL_DETAIL = (
    'Unsupported webhook URL — paste a WeCom / DingTalk / Feishu / '
    'Slack bot webhook'
)

# A file sent through a bot must live under one of these roots. The
# agent supplies the path, so we expand ~, resolve symlinks, and confirm
# the real location is inside a known-safe tree — task captures land in
# /tmp and browser downloads under ~/.vibe-seller/downloads. We allow
# ONLY those, NOT the whole ~/.vibe-seller runtime dir (which holds the
# DB, jwt_secret, store notes) — that would let the agent exfiltrate
# secrets through a bot. This also blocks reads like ~/.ssh.
_ALLOWED_FILE_ROOTS = (
    Path('/tmp').resolve(),
    Path('/private/tmp').resolve(),
    DOWNLOADS_DIR.resolve(),
)


def _resolve_safe_file(raw_path: str) -> Path:
    """Resolve a caller-supplied path, rejecting anything outside the
    allowed roots. Expands ``~`` and requires an absolute path so the
    documented ``~/.vibe-seller/downloads/...`` usage works. Raises
    HTTPException(400) on traversal."""
    expanded = os.path.expanduser(raw_path)
    if not os.path.isabs(expanded):
        raise HTTPException(
            status_code=400, detail='File path must be absolute'
        )
    try:
        resolved = Path(expanded).resolve()
    except (OSError, RuntimeError):
        raise HTTPException(status_code=400, detail='Invalid file path')
    for root in _ALLOWED_FILE_ROOTS:
        if resolved == root or root in resolved.parents:
            return resolved
    raise HTTPException(
        status_code=400,
        detail='File path is outside the allowed directories',
    )


def _platform_or_400(url: str) -> Platform:
    """The platform a webhook URL belongs to, or a 400."""
    platform = base.platform_for_url(url)
    if platform is None:
        raise HTTPException(status_code=400, detail=UNSUPPORTED_URL_DETAIL)
    return platform


def _platform_info(platform: Platform) -> dict:
    """Registry metadata for the frontend (and the platform picker)."""
    return {
        'name': platform.name,
        'label': platform.label,
        'hosts': list(platform.hosts),
        'path_prefix': platform.path_prefix,
        'uses_secret': platform.uses_secret,
        'supports_file': platform.supports_file,
        'max_message': platform.max_message,
        'limits': dict(platform.limits),
        'limit_unit': platform.limit_unit,
        'doc_url': platform.doc_url,
    }


def _summary(bot: ChatBot) -> ChatBotSummary:
    platform = PLATFORMS.get(bot.platform)
    return ChatBotSummary(
        id=bot.id,
        name=bot.name,
        platform=bot.platform,
        webhook_url_masked=base.mask_webhook_url(bot.webhook_url, bot.platform),
        has_secret=bool(bot.secret),
        can_send_file=bool(platform and platform.supports_file),
        created_at=bot.created_at,
        updated_at=bot.updated_at,
    )


def _response(bot: ChatBot) -> ChatBotResponse:
    platform = PLATFORMS.get(bot.platform)
    return ChatBotResponse(
        id=bot.id,
        name=bot.name,
        webhook_url=bot.webhook_url,
        platform=bot.platform,
        secret=bot.secret,
        has_secret=bool(bot.secret),
        can_send_file=bool(platform and platform.supports_file),
        created_at=bot.created_at,
        updated_at=bot.updated_at,
    )


@router.get('/api/chat-bots/platforms')
async def list_chat_bot_platforms(
    _user: User = Depends(get_current_user),
):
    """The platform registry: labels, hosts, capabilities, limits.

    The frontend renders the platform badge from this — it never
    duplicates the table. The SERVER stays authoritative: it derives
    the platform from the URL again on create/update.
    """
    return [_platform_info(p) for p in PLATFORMS.values()]


@router.get('/api/chat-bots', response_model=list[ChatBotSummary])
async def list_chat_bots(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """List bots with the webhook URL masked (secret protection)."""
    result = await db.execute(
        select(ChatBot).order_by(ChatBot.created_at.desc())
    )
    return [_summary(b) for b in result.scalars().all()]


@router.get('/api/chat-bots/{bot_id}', response_model=ChatBotResponse)
async def get_chat_bot(
    bot_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Return a single bot with the full webhook URL (for edit)."""
    bot = await db.get(ChatBot, bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail='Bot not found')
    return _response(bot)


@router.post('/api/chat-bots', response_model=ChatBotResponse)
async def create_chat_bot(
    data: ChatBotCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Create a new chat bot config.

    The platform comes from the webhook URL, not from the request.
    """
    name = (data.name or '').strip()
    url = (data.webhook_url or '').strip()
    if not name:
        raise HTTPException(status_code=400, detail='Name is required')
    if not url:
        raise HTTPException(status_code=400, detail='Webhook URL is required')

    platform = _platform_or_400(url)
    secret = (data.secret or '').strip()
    if secret and not platform.uses_secret:
        raise HTTPException(
            status_code=400,
            detail=(f'{platform.label} webhooks do not use a signing secret'),
        )

    bot = ChatBot(
        name=name,
        webhook_url=url,
        platform=platform.name,
        secret=secret or None,
        created_by=user.id,
    )
    db.add(bot)
    await db.commit()
    await db.refresh(bot)
    return _response(bot)


@router.put('/api/chat-bots/{bot_id}', response_model=ChatBotResponse)
async def update_chat_bot(
    bot_id: str,
    data: ChatBotUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Update bot fields. Missing fields are left untouched; blank
    or None values for required fields are rejected with a 400.

    Changing the webhook URL re-derives the platform. A signing
    secret that no longer applies (the platform moved to WeCom or
    Slack) is cleared rather than kept around.
    """
    bot = await db.get(ChatBot, bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail='Bot not found')

    update = data.model_dump(exclude_unset=True)

    platform_name = bot.platform
    if 'webhook_url' in update:
        url = update['webhook_url']
        if url is None:
            raise HTTPException(
                status_code=400, detail='webhook_url cannot be null'
            )
        url = url.strip()
        if not url:
            raise HTTPException(
                status_code=400, detail='webhook_url cannot be blank'
            )
        platform_name = _platform_or_400(url).name
        bot.webhook_url = url
        bot.platform = platform_name

    if 'name' in update:
        name = update['name']
        if name is None:
            raise HTTPException(status_code=400, detail='name cannot be null')
        name = name.strip()
        if not name:
            raise HTTPException(status_code=400, detail='name cannot be blank')
        bot.name = name

    if 'secret' in update:
        secret = (update['secret'] or '').strip()
        platform = PLATFORMS.get(platform_name)
        if secret and not (platform and platform.uses_secret):
            raise HTTPException(
                status_code=400,
                detail=(
                    f'{platform.label if platform else platform_name} '
                    'webhooks do not use a signing secret'
                ),
            )
        bot.secret = secret or None

    # A secret that the (possibly new) platform cannot use is dropped:
    # keeping it would leave a secret on a row that never signs with it.
    platform = PLATFORMS.get(platform_name)
    if bot.secret and not (platform and platform.uses_secret):
        bot.secret = None

    bot.updated_at = datetime.now(UTC).isoformat()
    await db.commit()
    await db.refresh(bot)
    return _response(bot)


@router.delete('/api/chat-bots/{bot_id}')
async def delete_chat_bot(
    bot_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Delete a chat bot config."""
    bot = await db.get(ChatBot, bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail='Bot not found')
    await db.delete(bot)
    await db.commit()
    return {'ok': True}


@router.post('/api/chat-bots/{bot_id}/test')
async def test_chat_bot(
    bot_id: str,
    data: ChatBotTestRequest | None = None,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Send a test message to verify the webhook works."""
    bot = await db.get(ChatBot, bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail='Bot not found')

    raw = data.content if data else None
    content = (raw.strip() if raw else '') or DEFAULT_TEST_MESSAGE
    ok, err = await base.send_message(
        bot.platform, bot.webhook_url, bot.secret, content
    )
    return {'ok': ok, 'message': err or 'Message sent'}


@router.post('/api/chat-bots/{bot_id}/send')
async def send_chat_message(
    bot_id: str,
    data: ChatBotSendRequest,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Post a real message (text or markdown) through the bot."""
    bot = await db.get(ChatBot, bot_id)
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

    ok, err = await base.send_message(
        bot.platform, bot.webhook_url, bot.secret, content, msgtype=data.msgtype
    )
    return {'ok': ok, 'message': err or 'Message sent'}


@router.post('/api/chat-bots/{bot_id}/send-file')
async def send_chat_file(
    bot_id: str,
    data: ChatBotSendFileRequest,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Upload a local file and post it to the group via the bot.

    The webhook secret never leaves the server; the caller passes only
    the bot id and a server-local file path (validated against the
    allowed roots). Only WeCom group bots can carry files — the other
    platforms have no file endpoint, so the request is refused BEFORE
    the path is even looked at.
    """
    bot = await db.get(ChatBot, bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail='Bot not found')

    platform = PLATFORMS.get(bot.platform)
    label = platform.label if platform else bot.platform
    if not (platform and platform.supports_file):
        raise HTTPException(
            status_code=400,
            detail=(
                f'File sending is not supported for {label} webhooks '
                '(only WeCom).'
            ),
        )

    raw = (data.path or '').strip()
    if not raw:
        raise HTTPException(status_code=400, detail='path cannot be blank')
    safe_path = _resolve_safe_file(raw)

    ok, err = await send_file_webhook(bot.webhook_url, str(safe_path))
    return {'ok': ok, 'message': err or 'File sent'}
