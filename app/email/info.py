"""The mailbox info an agent reads: one builder for every route.

``GET /api/tasks/{id}/mailboxes`` (the in-task ``vibe_seller_email_info``)
and ``GET /api/email-accounts/info-by-store/{store}`` (the operator
toolset) both return :func:`mailbox_info`. What each mailbox can do comes
from ``app.email.access``, the one place that decides.
"""

import asyncio

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import telemetry
from app.email.access import (
    NO_MAILBOX,
    NO_STORE_MAILBOX,
    mailbox_files,
    mailboxes_for,
)
from app.email.db import init_email_db
from app.models.store import Store
from app.telemetry_events import TelemetryEvent
from app.telemetry_via import current_via


async def resolve_store(db: AsyncSession, store: str) -> Store:
    """The store named by id or slug; 404 when it names none.

    Agents see a store's slug in every path (``stores/<slug>/``) and pass
    it as often as the id. A value that names no store is refused, never
    answered as "no mailbox": that would tell an agent it cannot do what
    it can.
    """
    found = await db.get(Store, store)
    if found is None:
        found = (
            await db.execute(select(Store).where(Store.slug == store))
        ).scalar_one_or_none()
    if found is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f'No store {store!r}. Pass the store id, or its slug (the '
                'folder name under stores/).'
            ),
        )
    return found


async def mailbox_info(db: AsyncSession, store_id: str | None) -> dict:
    """Mailboxes for *store_id* (every one when None) and how to read them."""
    mailboxes = await mailboxes_for(db, store_id)
    if not mailboxes:
        # No schema or sample queries for databases that do not exist:
        # the answer is that there is no mail here, said plainly.
        return {
            'store_id': store_id,
            'accounts': [],
            'note': NO_STORE_MAILBOX if store_id else NO_MAILBOX,
        }
    accounts = []
    for m in mailboxes:
        await asyncio.to_thread(init_email_db, m.account_id)
        db_path, attachments_dir = mailbox_files(m.account_id)
        accounts.append({
            **m.as_dict(),
            'stores': list(m.stores),
            'db_path': str(db_path),
            'attachments_dir': str(attachments_dir),
        })
    telemetry.send(
        TelemetryEvent.EMAIL_READ,
        {
            'source': 'info',
            'provider_kinds': sorted({
                telemetry.email_provider_kind(m.email) for m in mailboxes
            }),
            'via': current_via(),
        },
    )
    return {
        'store_id': store_id,
        'accounts': accounts,
        'schema': (
            'emails(message_id, folder, subject, sender, '
            'recipient, date, body_text, body_html, '
            'raw_headers, attachments, flags, fetched_at, '
            'email_account, received_epoch)'
        ),
        # date vs received_epoch is the footgun that leaked a run's
        # already-processed email (tests/e2e/test_email_watermark_e2e.py):
        # `date` is a human ISO string, the watermark is unix seconds, and
        # SQLite treats every TEXT value as greater than any INTEGER — so
        # `WHERE date > <watermark>` matches EVERY row and silently leaks.
        'column_notes': (
            '`date` = sender-supplied ISO string, for DISPLAY only. Never '
            'compare it with >/< against the epoch watermark. For any '
            '"since <time>" filter use `received_epoch` (unix seconds, '
            'arrival axis) — that is the axis the email_watermark cursor '
            'is measured in, so `received_epoch > <cursor>` is a correct '
            'INTEGER compare. One sync gives every message it fetches the '
            'same received_epoch, so for newest first order by '
            '`received_epoch DESC, date DESC` (`date` is UTC ISO, so it '
            'sorts).'
        ),
        # Metadata-only samples (subject/sender/date — never body_text
        # or SELECT *). For a "new since last run" sweep, do NOT widen
        # these into an unfiltered body query: it pulls
        # already-processed emails into the agent context and leaks them
        # into the run (see tests/e2e/test_email_watermark_e2e.py). Use
        # vibe_seller_get_new_emails instead — see watermark_note.
        'sample_queries': [
            # Newest first. One sync stamps every message it fetches
            # with the same received_epoch (the first sync: the whole
            # inbox), so `date` breaks the tie; without it the "latest"
            # is any message of that batch (observed on a real inbox).
            'sqlite3 <db_path> "SELECT subject, sender, '
            "date FROM emails WHERE folder='INBOX' "
            'ORDER BY received_epoch DESC, date DESC LIMIT 20"',
            'sqlite3 <db_path> "SELECT subject, sender, date '
            "FROM emails WHERE subject LIKE '%keyword%'\"",
            # Time window: compare received_epoch (INTEGER), never date.
            'sqlite3 <db_path> "SELECT subject, sender, date FROM '
            'emails WHERE received_epoch > <watermark_epoch> '
            'ORDER BY received_epoch ASC"',
        ],
        'watermark_note': (
            'For a scheduled "new emails since the last run" sweep, do '
            'NOT query here — call vibe_seller_get_new_emails. It reads '
            'the email_watermark cursor and filters server-side, so '
            'already-processed emails never enter your context. If you '
            'do query raw, filter on received_epoch (NOT date).'
        ),
        'sync_interval': '5 minutes (automatic)',
    }
