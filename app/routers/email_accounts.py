"""Email account CRUD, store-email links, SMTP send."""

import asyncio
from datetime import UTC, datetime
import imaplib
import logging
import smtplib

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import telemetry
from app.auth import get_current_user
from app.channels.email_discovery import (
    discover_imap,
    discover_smtp,
)
from app.channels.email_poller import sync_account_emails
from app.database import get_db
from app.email.db import db_path_for_account, get_sync_state, init_email_db
from app.email.sender import send_email
from app.models.email_account import EmailAccount
from app.models.store import Store
from app.models.store_email_link import StoreEmailLink
from app.models.user import User
from app.scheduler.email_sync import maybe_seed_sync_state
from app.schemas.email_account import (
    EmailAccountCreate,
    EmailAccountResponse,
    EmailAccountUpdate,
    ImapDiscoverResponse,
    SmtpDiscoverResponse,
    StoreEmailLinkCreate,
    StoreEmailLinkResponse,
)
from app.telemetry_events import TelemetryEvent
from app.utils.crypto import decrypt_password, encrypt_password

logger = logging.getLogger(__name__)

router = APIRouter(tags=['email-accounts'])


# ── Email Account CRUD ──────────────────────────────────────


@router.get(
    '/api/email-accounts/discover',
    response_model=ImapDiscoverResponse,
)
async def discover_imap_settings(
    email: str = Query(..., description='Email address'),
    _user: User = Depends(get_current_user),
):
    """Auto-discover IMAP settings for an email address."""
    result = discover_imap(email)
    if result:
        return ImapDiscoverResponse(
            imap_host=result[0],
            imap_port=result[1],
            source=result[2],
        )
    return ImapDiscoverResponse(
        imap_host=None, imap_port=None, source='unknown'
    )


@router.get(
    '/api/email-accounts/discover-smtp',
    response_model=SmtpDiscoverResponse,
)
async def discover_smtp_settings(
    email: str = Query(..., description='Email address'),
    _user: User = Depends(get_current_user),
):
    """Auto-discover SMTP settings for an email address."""
    result = discover_smtp(email)
    if result:
        return SmtpDiscoverResponse(
            smtp_host=result[0],
            smtp_port=result[1],
            smtp_use_starttls=result[2],
            source=result[3],
        )
    return SmtpDiscoverResponse(
        smtp_host=None,
        smtp_port=None,
        smtp_use_starttls=None,
        source='unknown',
    )


class _TestConnectionRequest(BaseModel):
    email: str
    imap_host: str
    imap_port: int = 993
    password: str
    use_ssl: bool = True


@router.post('/api/email-accounts/test')
async def test_connection(
    data: _TestConnectionRequest,
    _user: User = Depends(get_current_user),
):
    """Test IMAP connection with raw credentials (before saving)."""

    def _test_sync():
        try:
            if data.use_ssl:
                mail = imaplib.IMAP4_SSL(data.imap_host, data.imap_port)
            else:
                mail = imaplib.IMAP4(data.imap_host, data.imap_port)
            mail.login(data.email, data.password)
            mail.logout()
            return {'ok': True, 'message': 'Connection successful'}
        except imaplib.IMAP4.error as e:
            return {
                'ok': False,
                'message': f'IMAP login failed: {e}',
            }
        except Exception as e:
            return {
                'ok': False,
                'message': f'Connection failed: {e}',
            }

    return await asyncio.to_thread(_test_sync)


@router.post(
    '/api/email-accounts',
    response_model=EmailAccountResponse,
)
async def create_email_account(
    data: EmailAccountCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Create a new email account with encrypted password."""
    # Auto-discover IMAP if not provided
    imap_host = data.imap_host
    imap_port = data.imap_port
    if not imap_host:
        discovered = discover_imap(data.email)
        if discovered:
            imap_host = discovered[0]
            imap_port = discovered[1]
        else:
            raise HTTPException(
                status_code=400,
                detail='Could not auto-discover IMAP settings. '
                'Please provide imap_host manually.',
            )

    # Check for duplicate email
    existing = await db.execute(
        select(EmailAccount).where(EmailAccount.email == data.email)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail='Email account already exists',
        )

    # Auto-discover SMTP if not provided
    smtp_host = data.smtp_host
    smtp_port = data.smtp_port
    smtp_use_tls = data.smtp_use_tls
    if not smtp_host:
        smtp_result = discover_smtp(data.email)
        if smtp_result:
            smtp_host = smtp_result[0]
            smtp_port = smtp_result[1]
            smtp_use_tls = smtp_result[2]

    account = EmailAccount(
        email=data.email,
        encrypted_password=encrypt_password(data.password),
        imap_host=imap_host,
        imap_port=imap_port,
        use_ssl=data.use_ssl,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_use_tls=smtp_use_tls,
        created_by=user.id,
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    telemetry.send(
        TelemetryEvent.EMAIL_ACCOUNT_ADDED,
        {
            'provider_kind': telemetry.email_provider_kind(data.email),
            'has_smtp': bool(smtp_host),
            'use_ssl': bool(data.use_ssl),
        },
    )
    return account


@router.get(
    '/api/email-accounts',
    response_model=list[EmailAccountResponse],
)
async def list_email_accounts(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """List all email accounts (passwords never exposed)."""
    result = await db.execute(
        select(EmailAccount).order_by(EmailAccount.created_at.desc())
    )
    return result.scalars().all()


@router.put(
    '/api/email-accounts/{account_id}',
    response_model=EmailAccountResponse,
)
async def update_email_account(
    account_id: str,
    data: EmailAccountUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Update an email account."""
    account = await db.get(EmailAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail='Email account not found')

    update_data = data.model_dump(exclude_unset=True)
    # Handle password encryption separately
    if 'password' in update_data:
        raw_pw = update_data.pop('password')
        if raw_pw is not None:
            account.encrypted_password = encrypt_password(raw_pw)

    for field, value in update_data.items():
        setattr(account, field, value)

    account.updated_at = datetime.now(UTC).isoformat()
    await db.commit()
    await db.refresh(account)
    return account


@router.delete('/api/email-accounts/{account_id}')
async def delete_email_account(
    account_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Delete email account and cascade store links."""
    account = await db.get(EmailAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail='Email account not found')

    # Delete store links first
    await db.execute(
        delete(StoreEmailLink).where(
            StoreEmailLink.email_account_id == account_id
        )
    )
    await db.delete(account)
    await db.commit()
    telemetry.send(TelemetryEvent.EMAIL_ACCOUNT_REMOVED, {})
    return {'ok': True}


@router.post('/api/email-accounts/{account_id}/test')
async def test_email_account(
    account_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Test IMAP connection for an email account."""
    account = await db.get(EmailAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail='Email account not found')

    password = decrypt_password(account.encrypted_password)

    def _test_sync():
        try:
            if account.use_ssl:
                mail = imaplib.IMAP4_SSL(account.imap_host, account.imap_port)
            else:
                mail = imaplib.IMAP4(account.imap_host, account.imap_port)
            mail.login(account.email, password)
            mail.logout()
            return {'ok': True, 'message': 'Connection successful'}
        except imaplib.IMAP4.error as e:
            return {
                'ok': False,
                'message': f'IMAP login failed: {e}',
            }
        except Exception as e:
            return {
                'ok': False,
                'message': f'Connection failed: {e}',
            }

    return await asyncio.to_thread(_test_sync)


@router.post('/api/email-accounts/{account_id}/test-smtp')
async def test_smtp_account(
    account_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Test SMTP connection for an email account."""
    account = await db.get(EmailAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail='Email account not found')
    if not account.smtp_host:
        return {
            'ok': False,
            'message': 'No SMTP host configured',
        }

    password = decrypt_password(account.encrypted_password)

    def _test_smtp():
        try:
            if account.smtp_use_tls:
                server = smtplib.SMTP(
                    account.smtp_host, account.smtp_port or 587
                )
                server.ehlo()
                server.starttls()
                server.ehlo()
            else:
                server = smtplib.SMTP_SSL(
                    account.smtp_host, account.smtp_port or 465
                )
            server.login(account.email, password)
            server.quit()
            return {
                'ok': True,
                'message': 'SMTP connection successful',
            }
        except smtplib.SMTPAuthenticationError as e:
            return {
                'ok': False,
                'message': f'SMTP auth failed: {e}',
            }
        except Exception as e:
            return {
                'ok': False,
                'message': f'SMTP connection failed: {e}',
            }

    return await asyncio.to_thread(_test_smtp)


class _SendEmailRequest(BaseModel):
    to: str | list[str]
    subject: str
    body: str
    body_html: str | None = None


@router.post('/api/email-accounts/{account_id}/send')
async def send_email_endpoint(
    account_id: str,
    data: _SendEmailRequest,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Send an email via SMTP."""
    account = await db.get(EmailAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail='Email account not found')

    password = decrypt_password(account.encrypted_password)
    result = await send_email(
        account=account,
        password=password,
        to=data.to,
        subject=data.subject,
        body=data.body,
        body_html=data.body_html,
    )
    if not result.get('ok'):
        raise HTTPException(
            status_code=502,
            detail=result.get('error', 'Send failed'),
        )
    return result


class _SyncEmailNowRequest(BaseModel):
    account_email: str


@router.post('/api/email-accounts/sync-now')
async def sync_email_now(
    data: _SyncEmailNowRequest,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Trigger immediate IMAP sync for one email account.

    Blocks until sync completes. Returns new email count,
    account email, and last-polled timestamp. If the account
    was synced within the last 30 seconds, returns the cached
    state without re-syncing. Agent then queries the
    per-account SQLite DB directly via sqlite3 CLI.
    """
    result = await db.execute(
        select(EmailAccount).where(EmailAccount.email == data.account_email)
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(
            status_code=404,
            detail=(f'No account found for {data.account_email}'),
        )

    # Cooldown: skip sync if polled within last 30 seconds
    _SYNC_COOLDOWN_SECONDS = 30
    state = await asyncio.to_thread(get_sync_state, account.id, 'INBOX')
    if state.get('last_polled_at'):
        last = datetime.fromisoformat(state['last_polled_at'])
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        age = (datetime.now(UTC) - last).total_seconds()
        if age < _SYNC_COOLDOWN_SECONDS:
            return {
                'ok': True,
                'account_email': data.account_email,
                'new_emails': 0,
                'last_polled_at': state['last_polled_at'],
                'skipped': True,
            }

    await maybe_seed_sync_state(account.id)

    password = decrypt_password(account.encrypted_password)
    try:
        new_count = await sync_account_emails(account, password)
    except Exception as e:
        logger.exception(
            'On-demand sync failed for %s',
            data.account_email,
        )
        raise HTTPException(
            status_code=502,
            detail=f'Sync failed: {e}',
        ) from e

    state = await asyncio.to_thread(get_sync_state, account.id, 'INBOX')

    return {
        'ok': True,
        'account_email': data.account_email,
        'new_emails': new_count,
        'last_polled_at': state.get('last_polled_at'),
    }


@router.get('/api/email-accounts/info-by-store/{store_id}')
async def email_info_by_store(
    store_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Return email DB paths + schema for agents/MCP.

    Per-account: email address, DB path, attachments dir,
    schema summary, sample sqlite3 queries.
    """
    result = await db.execute(
        select(StoreEmailLink, EmailAccount)
        .join(
            EmailAccount,
            StoreEmailLink.email_account_id == EmailAccount.id,
        )
        .where(StoreEmailLink.store_id == store_id)
    )
    rows = result.all()

    accounts = []
    for _link, acct in rows:
        init_email_db(acct.id)
        path = db_path_for_account(acct.id)
        accounts.append({
            'account_id': acct.id,
            'email': acct.email,
            'db_path': str(path),
            'attachments_dir': str(
                path.parent / f'email_{acct.id}_attachments'
            ),
            'smtp_configured': bool(acct.smtp_host),
        })

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
            'INTEGER compare.'
        ),
        # Metadata-only samples (subject/sender/date — never body_text
        # or SELECT *). For a "new since last run" sweep, do NOT widen
        # these into an unfiltered body query: it pulls
        # already-processed emails into the agent context and leaks them
        # into the run (see tests/e2e/test_email_watermark_e2e.py). Use
        # vibe_seller_get_new_emails instead — see watermark_note.
        'sample_queries': [
            'sqlite3 <db_path> "SELECT subject, sender, '
            "date FROM emails WHERE folder='INBOX' "
            'ORDER BY received_epoch DESC LIMIT 20"',
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


# ── Email Account Links ──────────────────────────────────────


@router.get('/api/email-accounts/{account_id}/links')
async def get_email_account_links(
    account_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Get all store links for an email account."""
    account = await db.get(EmailAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail='Email account not found')

    result = await db.execute(
        select(StoreEmailLink, Store)
        .join(Store, StoreEmailLink.store_id == Store.id)
        .where(StoreEmailLink.email_account_id == account_id)
        .order_by(StoreEmailLink.created_at.desc())
    )
    rows = result.all()
    return [
        {
            'link_id': link.id,
            'store_id': link.store_id,
            'store_name': store.name,
            'watermark_date': link.watermark_date,
            'last_polled_at': link.last_polled_at,
        }
        for link, store in rows
    ]


# ── Store-Email Links ────────────────────────────────────────


@router.get(
    '/api/stores/{store_id}/emails',
    response_model=list[StoreEmailLinkResponse],
)
async def list_store_emails(
    store_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """List email accounts linked to a store."""
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail='Store not found')

    result = await db.execute(
        select(StoreEmailLink, EmailAccount)
        .join(
            EmailAccount,
            StoreEmailLink.email_account_id == EmailAccount.id,
        )
        .where(StoreEmailLink.store_id == store_id)
        .order_by(StoreEmailLink.created_at.desc())
    )
    rows = result.all()
    return [
        StoreEmailLinkResponse(
            id=link.id,
            store_id=link.store_id,
            email_account_id=link.email_account_id,
            email=acct.email,
            watermark_date=link.watermark_date,
            last_polled_at=link.last_polled_at,
        )
        for link, acct in rows
    ]


@router.post(
    '/api/stores/{store_id}/emails',
    response_model=StoreEmailLinkResponse,
)
async def link_email_to_store(
    store_id: str,
    data: StoreEmailLinkCreate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Link an email account to a store."""
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail='Store not found')

    account = await db.get(EmailAccount, data.email_account_id)
    if not account:
        raise HTTPException(status_code=404, detail='Email account not found')

    # Check for duplicate link
    existing = await db.execute(
        select(StoreEmailLink).where(
            StoreEmailLink.store_id == store_id,
            StoreEmailLink.email_account_id == data.email_account_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail='Email already linked to this store',
        )

    link = StoreEmailLink(
        store_id=store_id,
        email_account_id=data.email_account_id,
    )
    db.add(link)
    await db.commit()
    await db.refresh(link)

    return StoreEmailLinkResponse(
        id=link.id,
        store_id=link.store_id,
        email_account_id=link.email_account_id,
        email=account.email,
        watermark_date=link.watermark_date,
        last_polled_at=link.last_polled_at,
    )


@router.delete('/api/stores/{store_id}/emails/{link_id}')
async def unlink_email_from_store(
    store_id: str,
    link_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Unlink an email account from a store."""
    link = await db.get(StoreEmailLink, link_id)
    if not link or link.store_id != store_id:
        raise HTTPException(
            status_code=404,
            detail='Store email link not found',
        )
    await db.delete(link)
    await db.commit()
    return {'ok': True}
