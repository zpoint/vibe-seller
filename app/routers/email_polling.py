"""Store-scoped IMAP polling endpoint and helpers."""

import asyncio
from datetime import UTC, datetime, timedelta
import email as email_lib
from email.header import decode_header
import imaplib
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.email_account import EmailAccount
from app.models.store import Store
from app.models.store_email_link import StoreEmailLink
from app.models.user import User
from app.utils.crypto import decrypt_password

logger = logging.getLogger(__name__)

router = APIRouter(tags=['email-accounts'])

# Per-link poll locks to prevent concurrent watermark races
_poll_locks: dict[str, asyncio.Lock] = {}

MAX_SEEN_IDS = 500
POLL_COOLDOWN_SECONDS = 60
DEFAULT_LOOKBACK_DAYS = 7


@router.post('/api/stores/{store_id}/emails/poll')
async def poll_store_emails(
    store_id: str,
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Poll new emails for all linked accounts of a store."""
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail='Store not found')

    # Get all links with their email accounts
    result = await db.execute(
        select(StoreEmailLink, EmailAccount)
        .join(
            EmailAccount,
            StoreEmailLink.email_account_id == EmailAccount.id,
        )
        .where(StoreEmailLink.store_id == store_id)
    )
    rows = result.all()

    if not rows:
        return {'emails': [], 'count': 0, 'total': 0}

    all_emails: list[dict] = []
    now = datetime.now(UTC)

    for link, account in rows:
        # Rate limit: skip if polled less than 60s ago
        if link.last_polled_at:
            last = datetime.fromisoformat(link.last_polled_at)
            if (now - last).total_seconds() < POLL_COOLDOWN_SECONDS:
                logger.debug(
                    'Skipping poll for %s (cooldown)',
                    account.email,
                )
                continue

        # Per-link lock to prevent concurrent poll races
        if link.id not in _poll_locks:
            _poll_locks[link.id] = asyncio.Lock()

        async with _poll_locks[link.id]:
            emails = await _poll_link(link, account)
            all_emails.extend(emails)

            # Persist watermark updates
            await db.commit()

    total = len(all_emails)
    # limit=0 means return all (no pagination)
    page = all_emails if limit == 0 else all_emails[offset : offset + limit]
    return {
        'emails': page,
        'count': len(page),
        'total': total,
    }


async def _poll_link(
    link: StoreEmailLink,
    account: EmailAccount,
) -> list[dict]:
    """Poll a single email link for new messages."""
    password = decrypt_password(account.encrypted_password)

    # Compute search-since date
    if link.watermark_date:
        since_date = datetime.fromisoformat(link.watermark_date).date()
    else:
        since_date = (
            datetime.now(UTC) - timedelta(days=DEFAULT_LOOKBACK_DAYS)
        ).date()

    # Load previously seen message IDs
    seen_ids: set[str] = set()
    if link.seen_message_ids:
        try:
            seen_ids = set(json.loads(link.seen_message_ids))
        except (json.JSONDecodeError, TypeError):
            seen_ids = set()

    def _fetch_sync() -> list[dict]:
        messages: list[dict] = []
        try:
            if account.use_ssl:
                mail = imaplib.IMAP4_SSL(account.imap_host, account.imap_port)
            else:
                mail = imaplib.IMAP4(account.imap_host, account.imap_port)

            mail.login(account.email, password)
            mail.select('INBOX', readonly=True)

            # Search for messages since watermark date
            date_str = since_date.strftime('%d-%b-%Y')
            status, data = mail.search(None, f'(SINCE {date_str})')
            if status != 'OK':
                mail.logout()
                return messages

            msg_nums = data[0].split()
            for num in msg_nums:
                status, msg_data = mail.fetch(num, '(RFC822)')
                if status != 'OK':
                    continue

                raw_email = msg_data[0][1]
                msg = email_lib.message_from_bytes(raw_email)

                message_id = msg.get('Message-ID', num.decode())
                if message_id in seen_ids:
                    continue

                # Decode subject
                subject = _decode_header_value(msg.get('Subject', ''))
                sender = msg.get('From', '')
                date_str_hdr = msg.get('Date', '')

                # Get body
                body = _extract_body(msg)

                messages.append({
                    'message_id': message_id,
                    'subject': subject,
                    'sender': sender,
                    'date': date_str_hdr,
                    'body': body,
                    'email_account': account.email,
                })

            mail.logout()
        except Exception as e:
            logger.error(
                'IMAP poll error for %s: %s',
                account.email,
                e,
            )

        return messages

    messages = await asyncio.to_thread(_fetch_sync)

    # Update watermark and seen IDs
    if messages:
        new_ids = {m['message_id'] for m in messages}
        all_seen = seen_ids | new_ids
        # Keep only last MAX_SEEN_IDS
        if len(all_seen) > MAX_SEEN_IDS:
            all_seen = set(list(all_seen)[-MAX_SEEN_IDS:])

        link.seen_message_ids = json.dumps(list(all_seen))
        link.watermark_date = datetime.now(UTC).isoformat()

    link.last_polled_at = datetime.now(UTC).isoformat()

    return messages


def _decode_header_value(raw_value: str) -> str:
    """Decode an email header value (e.g. Subject)."""
    if not raw_value:
        return ''
    decoded = decode_header(raw_value)
    parts = []
    for part, charset in decoded:
        if isinstance(part, bytes):
            parts.append(part.decode(charset or 'utf-8', errors='replace'))
        else:
            parts.append(part)
    return ' '.join(parts)


def _extract_body(msg) -> str:
    """Extract plain-text body from an email message."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == 'text/plain':
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or 'utf-8'
                    return payload.decode(charset, errors='replace')
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or 'utf-8'
            return payload.decode(charset, errors='replace')
    return ''
