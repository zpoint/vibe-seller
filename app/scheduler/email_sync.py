"""Periodic background sync for all email accounts.

Syncs each account's IMAP to its per-account SQLite DB.
Concurrency is limited by a semaphore to avoid overload.
"""

import asyncio
from datetime import UTC, datetime
import json
import logging

from sqlalchemy import select, update

from app.channels.email_poller import sync_account_emails
from app.database import async_session
from app.email.db import get_sync_state, init_email_db, update_sync_state
from app.models.email_account import EmailAccount
from app.models.store_email_link import StoreEmailLink
from app.utils.crypto import decrypt_password

logger = logging.getLogger(__name__)

# Max concurrent account syncs.
_MAX_CONCURRENT = 3


async def sync_all_email_accounts() -> None:
    """Sync all email accounts with limited concurrency.

    Uses a semaphore to limit concurrent IMAP connections,
    avoiding thundering-herd without serial stagger delays.
    Per-account exceptions are caught individually.
    """
    async with async_session() as db:
        result = await db.execute(select(EmailAccount))
        accounts = result.scalars().all()

    if not accounts:
        return

    logger.info(
        'Starting email sync for %d account(s)',
        len(accounts),
    )

    sem = asyncio.Semaphore(_MAX_CONCURRENT)

    async def _sync_one(account: EmailAccount) -> None:
        async with sem:
            try:
                new_count = await sync_and_record(account)
                if new_count:
                    logger.info(
                        'Synced %d new email(s) for %s',
                        new_count,
                        account.email,
                    )
            except Exception:
                logger.exception('Email sync failed for %s', account.email)

    await asyncio.gather(*[_sync_one(account) for account in accounts])


async def sync_and_record(account: EmailAccount) -> int:
    """Sync *account* once and record the outcome on its row.

    A failure stores its error (``sync_error``) and re-raises; a success
    clears it and stamps ``synced_at``. ``app/email/access.py`` reads
    both, so an agent is told a mailbox is not syncing instead of reading
    a stale copy as the inbox. Every sync goes through here: the
    scheduled job and the on-demand "sync now".
    """
    await maybe_seed_sync_state(account.id)
    try:
        password = decrypt_password(account.encrypted_password)
        new_count = await sync_account_emails(account, password)
    except Exception as exc:
        await _record(
            account.id, sync_error=str(exc)[:300] or type(exc).__name__
        )
        raise
    await _record(
        account.id, sync_error=None, synced_at=datetime.now(UTC).isoformat()
    )
    return new_count


async def _record(account_id: str, **values) -> None:
    async with async_session() as db:
        await db.execute(
            update(EmailAccount)
            .where(EmailAccount.id == account_id)
            .values(**values)
        )
        await db.commit()


# A "sync now" within this window returns the last sync instead of
# opening IMAP again (an agent polling for an OTP calls it repeatedly).
SYNC_COOLDOWN_SECONDS = 30


async def sync_now(account: EmailAccount) -> dict:
    """Sync *account* now, unless it synced within the cooldown.

    Raises what the sync raised (recorded by :func:`sync_and_record`).
    """
    # The per-account DB (and its sync_state table) must exist before
    # the cooldown read: a new account has never synced, and the first
    # "sync now" would die on `no such table: sync_state`.
    await maybe_seed_sync_state(account.id)
    state = await asyncio.to_thread(get_sync_state, account.id, 'INBOX')
    if state.get('last_polled_at'):
        last = datetime.fromisoformat(state['last_polled_at'])
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        if (datetime.now(UTC) - last).total_seconds() < SYNC_COOLDOWN_SECONDS:
            return {
                'ok': True,
                'account_email': account.email,
                'new_emails': 0,
                'last_polled_at': state['last_polled_at'],
                'skipped': True,
            }
    new_count = await sync_and_record(account)
    state = await asyncio.to_thread(get_sync_state, account.id, 'INBOX')
    return {
        'ok': True,
        'account_email': account.email,
        'new_emails': new_count,
        'last_polled_at': state.get('last_polled_at'),
    }


async def maybe_seed_sync_state(account_id: str) -> None:
    """Seed per-account sync_state from StoreEmailLink watermarks.

    Only runs if the per-account sync_state table is empty.
    """
    await asyncio.to_thread(init_email_db, account_id)
    state = await asyncio.to_thread(get_sync_state, account_id, 'INBOX')
    if state:
        return  # Already has sync state

    async with async_session() as db:
        result = await db.execute(
            select(StoreEmailLink).where(
                StoreEmailLink.email_account_id == account_id
            )
        )
        links = result.scalars().all()

    if not links:
        return

    # Find the oldest watermark across all links
    oldest_wm = None
    all_seen: list[str] = []
    for link in links:
        if link.watermark_date:
            if oldest_wm is None or link.watermark_date < oldest_wm:
                oldest_wm = link.watermark_date
        if link.seen_message_ids:
            try:
                all_seen.extend(json.loads(link.seen_message_ids))
            except (json.JSONDecodeError, TypeError):
                pass

    if oldest_wm:
        await asyncio.to_thread(
            update_sync_state,
            account_id,
            'INBOX',
            watermark_date=oldest_wm,
            seen_message_ids=list(set(all_seen)),
        )
        logger.info(
            'Seeded sync_state for account %s from StoreEmailLink watermark',
            account_id,
        )
