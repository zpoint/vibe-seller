"""Shared email polling and sync utility.

Provides two layers:
1. ``sync_account_emails()`` — per-account sync to local SQLite DB
   with rich extraction (HTML, attachments, sent folder).
2. ``poll_store_emails()`` — thin wrapper for backward compat: calls
   sync then queries the local DB for recent emails.
"""

import asyncio
from datetime import UTC, datetime, timedelta
import email as email_lib
from email.header import decode_header
from email.utils import parsedate_to_datetime
import hashlib
from html import unescape as html_unescape
import imaplib
import json
import logging
import os
import re

from sqlalchemy import select

from app.database import async_session
from app.email.db import (
    attachments_dir_for_account,
    get_sync_state,
    init_email_db,
    search_emails,
    store_emails,
    update_sync_state,
)
from app.models.email_account import EmailAccount
from app.models.store_email_link import StoreEmailLink
from app.utils.crypto import decrypt_password

logger = logging.getLogger(__name__)

# Per-link poll locks to prevent concurrent watermark races.
_poll_locks: dict[str, asyncio.Lock] = {}
# Per-account sync locks.
_sync_locks: dict[str, asyncio.Lock] = {}

MAX_SEEN_IDS = 500
POLL_COOLDOWN_SECONDS = 60
DEFAULT_LOOKBACK_DAYS = 7
MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024  # 10 MB

# Sent folder names to try (order matters).
_SENT_FOLDER_NAMES = [
    'Sent',
    'Sent Messages',
    'Sent Items',
    '[Gmail]/Sent Mail',
    'INBOX.Sent',
    '已发送',
]


# ── Per-account sync (new primary API) ──────────────


async def sync_account_emails(
    account: EmailAccount,
    password: str,
) -> int:
    """Sync emails for *account* into its per-account SQLite DB.

    Returns total number of *new* emails stored.
    """
    account_id = account.id

    # Per-account lock
    if account_id not in _sync_locks:
        _sync_locks[account_id] = asyncio.Lock()

    async with _sync_locks[account_id]:
        init_email_db(account_id)

        def _sync() -> int:
            return _sync_account(account, password)

        return await asyncio.to_thread(_sync)


def _sync_account(account: EmailAccount, password: str) -> int:
    """Synchronous per-account sync across INBOX + Sent.

    Raises on connection/auth/sync errors — callers are
    responsible for catching and handling.
    """
    if account.use_ssl:
        mail = imaplib.IMAP4_SSL(account.imap_host, account.imap_port)
    else:
        mail = imaplib.IMAP4(account.imap_host, account.imap_port)
    try:
        mail.login(account.email, password)

        # Sync INBOX
        total_new = _sync_folder(mail, account, password, 'INBOX')

        # Detect and sync Sent folder
        sent_folder = _detect_sent_folder(mail, account.id)
        if sent_folder:
            total_new += _sync_folder(mail, account, password, sent_folder)

        return total_new
    finally:
        try:
            mail.logout()
        except Exception:
            pass


def _detect_sent_folder(
    mail: imaplib.IMAP4_SSL | imaplib.IMAP4,
    account_id: str,
) -> str | None:
    """Detect the Sent folder via LIST command."""
    # Check if we already cached it
    state = get_sync_state(account_id, '__sent_folder__')
    if state.get('watermark_date'):
        return state['watermark_date']  # Reused field

    try:
        status, data = mail.list()
        if status != 'OK':
            return None

        for line in data:
            if not line:
                continue
            decoded = (
                line.decode('utf-8', errors='replace')
                if isinstance(line, bytes)
                else line
            )

            # Check for \Sent special-use flag (RFC 6154)
            if '\\Sent' in decoded:
                # Extract folder name
                folder = _parse_folder_name(decoded)
                if folder:
                    update_sync_state(
                        account_id,
                        '__sent_folder__',
                        watermark_date=folder,
                    )
                    return folder

        # Fallback: try known names
        known_folders: set[str] = set()
        for line in data:
            decoded = (
                line.decode('utf-8', errors='replace')
                if isinstance(line, bytes)
                else line
            )
            folder = _parse_folder_name(decoded)
            if folder:
                known_folders.add(folder)

        for name in _SENT_FOLDER_NAMES:
            if name in known_folders:
                update_sync_state(
                    account_id,
                    '__sent_folder__',
                    watermark_date=name,
                )
                return name

    except Exception as e:
        logger.debug('Sent folder detection failed: %s', e)

    return None


def _parse_folder_name(list_line: str) -> str | None:
    """Parse folder name from IMAP LIST response line."""
    # Format: (\\flags) "delimiter" "folder_name"
    # or: (\\flags) "delimiter" folder_name
    parts = list_line.rsplit('"', 2)
    if len(parts) >= 2:
        # Last quoted string is the folder name
        return parts[-2]
    # Try unquoted
    parts = list_line.rsplit(' ', 1)
    if len(parts) == 2:
        return parts[-1].strip('"')
    return None


def _sync_folder(
    mail: imaplib.IMAP4_SSL | imaplib.IMAP4,
    account: EmailAccount,
    password: str,
    folder: str,
) -> int:
    """Sync a single folder to the local DB."""
    account_id = account.id
    state = get_sync_state(account_id, folder)

    if state.get('watermark_date'):
        since_date = datetime.fromisoformat(state['watermark_date']).date()
    else:
        since_date = (
            datetime.now(UTC) - timedelta(days=DEFAULT_LOOKBACK_DAYS)
        ).date()

    seen_ids: set[str] = set(state.get('seen_message_ids', []))

    try:
        status, _count = mail.select(folder, readonly=True)
        if status != 'OK':
            return 0
    except imaplib.IMAP4.error:
        logger.debug('Cannot select folder %s', folder)
        return 0

    date_str = since_date.strftime('%d-%b-%Y')
    status, data = mail.search(None, f'(SINCE {date_str})')
    if status != 'OK':
        return 0

    msg_nums = data[0].split() if data[0] else []
    emails_to_store: list[dict] = []
    att_dir = attachments_dir_for_account(account_id)

    for num in msg_nums:
        status, msg_data = mail.fetch(num, '(RFC822 FLAGS)')
        if status != 'OK':
            continue

        raw_email = msg_data[0][1]
        msg = email_lib.message_from_bytes(raw_email)

        message_id = msg.get('Message-ID', num.decode())
        if message_id in seen_ids:
            continue

        # Extract flags from FETCH response metadata.
        # IMAP FETCH (RFC822 FLAGS) returns msg_data[0] as
        # (metadata_bytes, raw_email_bytes).  Flags are in
        # the metadata portion (msg_data[0][0]).
        flags_str = ''
        if msg_data[0][0]:
            meta = (
                msg_data[0][0].decode('utf-8', errors='replace')
                if isinstance(msg_data[0][0], bytes)
                else str(msg_data[0][0])
            )
            # Extract FLAGS portion, e.g. "1 (FLAGS (\\Seen) ..."
            flags_match = re.search(r'FLAGS\s*\(([^)]*)\)', meta)
            if flags_match:
                flags_str = flags_match.group(1)

        subject = _decode_header_value(msg.get('Subject', ''))
        sender = msg.get('From', '')
        recipient = msg.get('To', '')
        date_str_hdr = _normalize_date(msg.get('Date', ''))
        body_text = _extract_body(msg)
        body_html = _extract_html_body(msg)
        raw_headers = json.dumps(dict(msg.items()), ensure_ascii=False)
        attachment_meta = _extract_attachments(msg, att_dir, message_id)

        emails_to_store.append({
            'message_id': message_id,
            'folder': folder,
            'subject': subject,
            'sender': sender,
            'recipient': recipient,
            'date': date_str_hdr,
            'body_text': body_text,
            'body_html': body_html,
            'raw_headers': raw_headers,
            'attachments': json.dumps(attachment_meta, ensure_ascii=False)
            if attachment_meta
            else None,
            'flags': flags_str,
            'fetched_at': datetime.now(UTC).isoformat(),
            'email_account': account.email,
        })

        seen_ids.add(message_id)

    new_count = store_emails(account_id, emails_to_store)

    # Trim seen IDs — convert to list to preserve insertion order,
    # keeping the most recently added IDs.
    seen_list = list(seen_ids)
    if len(seen_list) > MAX_SEEN_IDS:
        seen_list = seen_list[-MAX_SEEN_IDS:]

    update_sync_state(
        account_id,
        folder,
        watermark_date=datetime.now(UTC).isoformat(),
        last_polled_at=datetime.now(UTC).isoformat(),
        seen_message_ids=seen_list,
    )

    return new_count


# ── Legacy poll wrapper ────────────────────────────────


async def poll_store_emails(
    store_id: str,
    advance_watermark: bool = True,
) -> list[dict]:
    """Poll new emails for every account linked to *store_id*.

    Backward-compat wrapper: syncs accounts, then queries
    the local SQLite DB for recent emails.
    """
    async with async_session() as db:
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
        return []

    all_emails: list[dict] = []

    for link, account in rows:
        password = decrypt_password(account.encrypted_password)

        if advance_watermark:
            try:
                await sync_account_emails(account, password)
            except Exception:
                logger.exception('Sync failed for %s', account.email)
        # Query local DB for recent emails
        since = (
            datetime.now(UTC) - timedelta(days=DEFAULT_LOOKBACK_DAYS)
        ).isoformat()
        rows_db = await asyncio.to_thread(
            search_emails,
            account.id,
            since,
            [],  # No keyword filter — return all
        )
        for r in rows_db:
            all_emails.append({
                'message_id': r.get('message_id', ''),
                'subject': r.get('subject', ''),
                'sender': r.get('sender', ''),
                'date': r.get('date', ''),
                'body': r.get('body_text', ''),
                'email_account': account.email,
            })

    return all_emails


# ── Internal helpers ────────────────────────────────


def _normalize_date(raw_date: str) -> str:
    """Parse RFC 2822 date to UTC ISO-8601 for sortable storage."""
    if not raw_date:
        return ''
    try:
        dt = parsedate_to_datetime(raw_date)
        return dt.astimezone(UTC).isoformat()
    except Exception:
        # Fallback: return raw if unparseable
        return raw_date


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


_WHITESPACE_RE = re.compile(r'[ \t]+')
_MULTI_NEWLINE_RE = re.compile(r'\n{3,}')


def _html_to_text(html: str) -> str:
    """Best-effort HTML → plain-text for SQL LIKE / search.

    Used as a fallback when an email ships only `text/html` (common
    for Amazon Seller Central notifications) so `body_text` is never
    empty. Not a full parser — drops scripts/styles/comments, turns
    common block tags into newlines, unescapes entities, and
    collapses whitespace.
    """
    if not html:
        return ''
    cleaned = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.S | re.I)
    cleaned = re.sub(r'<style[^>]*>.*?</style>', '', cleaned, flags=re.S | re.I)
    cleaned = re.sub(r'<!--.*?-->', '', cleaned, flags=re.S)
    cleaned = re.sub(r'<br\s*/?>', '\n', cleaned, flags=re.I)
    cleaned = re.sub(
        r'</(p|div|tr|li|h[1-6]|table|blockquote)>',
        '\n',
        cleaned,
        flags=re.I,
    )
    cleaned = re.sub(r'<[^>]+>', '', cleaned)
    cleaned = html_unescape(cleaned)
    # `&nbsp;` → U+00A0 after unescape. U+00A0 is NOT matched by
    # our `[ \t]+` collapse below, and SQL LIKE queries against
    # regular-space keywords would miss rows that happen to sit
    # next to the entity. Normalize to ASCII space up-front.
    cleaned = cleaned.replace('\u00a0', ' ')
    cleaned = _WHITESPACE_RE.sub(' ', cleaned)
    cleaned = _MULTI_NEWLINE_RE.sub('\n\n', cleaned)
    return cleaned.strip()


def _extract_body(msg) -> str:
    """Extract plain-text body from an email message.

    Falls back to stripping the HTML body when no ``text/plain``
    part is present (common for Amazon Seller Central notifications
    which ship HTML only). This keeps ``body_text`` populated so SQL
    LIKE queries and watermark reports see message content.
    """
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == 'text/plain':
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or 'utf-8'
                    return payload.decode(charset, errors='replace')
    else:
        ctype = msg.get_content_type()
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or 'utf-8'
            raw = payload.decode(charset, errors='replace')
            if ctype == 'text/html':
                return _html_to_text(raw)
            return raw
    # Multipart with no text/plain — fall back to HTML.
    html = _extract_html_body(msg)
    if html:
        return _html_to_text(html)
    return ''


def _extract_html_body(msg) -> str | None:
    """Extract HTML body from an email message."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == 'text/html':
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or 'utf-8'
                    return payload.decode(charset, errors='replace')
    else:
        if msg.get_content_type() == 'text/html':
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or 'utf-8'
                return payload.decode(charset, errors='replace')
    return None


def _extract_attachments(msg, att_dir, message_id: str) -> list[dict]:
    """Extract and save attachments. Skip files > 10 MB."""
    attachments = []
    if not msg.is_multipart():
        return attachments

    for part in msg.walk():
        disposition = part.get('Content-Disposition', '')
        if 'attachment' not in disposition:
            continue

        filename = part.get_filename()
        if not filename:
            continue
        filename = _decode_header_value(filename)

        content_type = part.get_content_type()
        payload = part.get_payload(decode=True)
        size = len(payload) if payload else 0

        meta: dict = {
            'filename': filename,
            'content_type': content_type,
            'size': size,
        }

        if payload and size <= MAX_ATTACHMENT_SIZE:
            # Sanitize filename
            safe_name = (
                filename.replace('/', '_').replace('\\', '_').replace('\0', '')
            )
            # Include message_id hash for uniqueness
            mid_hash = hashlib.sha256(message_id.encode()).hexdigest()[:8]
            save_name = f'{mid_hash}_{safe_name}'
            save_path = os.path.join(str(att_dir), save_name)
            with open(save_path, 'wb') as f:
                f.write(payload)
            meta['local_path'] = save_path
        # else: >10 MB — metadata only

        attachments.append(meta)

    return attachments
