"""Periodic background sync for all email accounts.

Syncs each account's IMAP to its per-account SQLite DB.
Concurrency is limited by a semaphore to avoid overload.
"""

import asyncio
import json
import logging

from sqlalchemy import select

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
                await maybe_seed_sync_state(account.id)
                password = decrypt_password(account.encrypted_password)
                new_count = await sync_account_emails(account, password)
                if new_count:
                    logger.info(
                        'Synced %d new email(s) for %s',
                        new_count,
                        account.email,
                    )
            except Exception:
                logger.exception('Email sync failed for %s', account.email)

    await asyncio.gather(*[_sync_one(account) for account in accounts])


async def maybe_seed_sync_state(account_id: str) -> None:
    """Seed per-account sync_state from StoreEmailLink watermarks.

    Only runs if the per-account sync_state table is empty.
    """
    init_email_db(account_id)
    state = get_sync_state(account_id, 'INBOX')
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
        update_sync_state(
            account_id,
            'INBOX',
            watermark_date=oldest_wm,
            seen_message_ids=list(set(all_seen)),
        )
        logger.info(
            'Seeded sync_state for account %s from StoreEmailLink watermark',
            account_id,
        )
