"""Per-account SQLite DB manager for email storage.

Each email account gets its own SQLite database file so that
agents can query emails directly via ``sqlite3`` CLI.  All
functions are synchronous — callers use ``asyncio.to_thread()``.
"""

from datetime import UTC, datetime
import json
import logging
from pathlib import Path
import sqlite3

from app.config import EMAIL_DBS_DIR

logger = logging.getLogger(__name__)

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS emails (
    message_id    TEXT NOT NULL,
    folder        TEXT NOT NULL DEFAULT 'INBOX',
    subject       TEXT,
    sender        TEXT,
    recipient     TEXT,
    date          TEXT,
    body_text     TEXT,
    body_html     TEXT,
    raw_headers   TEXT,
    attachments   TEXT,
    flags         TEXT,
    fetched_at    TEXT,
    email_account TEXT,
    PRIMARY KEY (message_id, folder)
);
CREATE INDEX IF NOT EXISTS idx_emails_sender ON emails(sender);
CREATE INDEX IF NOT EXISTS idx_emails_date   ON emails(date);
CREATE INDEX IF NOT EXISTS idx_emails_folder ON emails(folder);

CREATE TABLE IF NOT EXISTS sync_state (
    folder            TEXT PRIMARY KEY,
    watermark_date    TEXT,
    last_polled_at    TEXT,
    seen_message_ids  TEXT
);
"""


def email_db_dir() -> Path:
    """Return the directory for email DBs, creating if needed."""
    EMAIL_DBS_DIR.mkdir(parents=True, exist_ok=True)
    return EMAIL_DBS_DIR


def db_path_for_account(account_id: str) -> Path:
    """Return the DB file path for *account_id*."""
    return email_db_dir() / f'email_{account_id}.db'


def attachments_dir_for_account(account_id: str) -> Path:
    """Return the attachments directory for *account_id*."""
    d = email_db_dir() / f'email_{account_id}_attachments'
    d.mkdir(parents=True, exist_ok=True)
    return d


def init_email_db(account_id: str) -> Path:
    """Create the DB + tables if they don't exist.

    Enables WAL mode for read/write concurrency.
    Returns the DB file path.
    """
    path = db_path_for_account(account_id)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()
    return path


def store_emails(account_id: str, emails: list[dict]) -> int:
    """INSERT OR IGNORE emails into the account DB.

    Returns the number of newly inserted rows.
    """
    if not emails:
        return 0

    path = db_path_for_account(account_id)
    conn = sqlite3.connect(str(path))
    new_count = 0
    try:
        for em in emails:
            cur = conn.execute(
                'INSERT OR IGNORE INTO emails '
                '(message_id, folder, subject, sender, recipient,'
                ' date, body_text, body_html, raw_headers,'
                ' attachments, flags, fetched_at, email_account)'
                ' VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (
                    em.get('message_id', ''),
                    em.get('folder', 'INBOX'),
                    em.get('subject'),
                    em.get('sender'),
                    em.get('recipient'),
                    em.get('date'),
                    em.get('body_text'),
                    em.get('body_html'),
                    em.get('raw_headers'),
                    em.get('attachments'),
                    em.get('flags'),
                    em.get(
                        'fetched_at',
                        datetime.now(UTC).isoformat(),
                    ),
                    em.get('email_account'),
                ),
            )
            new_count += cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return new_count


def get_sync_state(account_id: str, folder: str) -> dict:
    """Read the sync watermark for *folder*."""
    path = db_path_for_account(account_id)
    conn = sqlite3.connect(str(path))
    try:
        row = conn.execute(
            'SELECT watermark_date, last_polled_at, '
            'seen_message_ids FROM sync_state WHERE folder=?',
            (folder,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return {}
    seen = []
    if row[2]:
        try:
            seen = json.loads(row[2])
        except (json.JSONDecodeError, TypeError):
            pass
    return {
        'watermark_date': row[0],
        'last_polled_at': row[1],
        'seen_message_ids': seen,
    }


def update_sync_state(
    account_id: str,
    folder: str,
    *,
    watermark_date: str | None = None,
    last_polled_at: str | None = None,
    seen_message_ids: list[str] | None = None,
) -> None:
    """Upsert sync state for *folder*."""
    path = db_path_for_account(account_id)
    conn = sqlite3.connect(str(path))
    try:
        seen_json = (
            json.dumps(seen_message_ids)
            if seen_message_ids is not None
            else None
        )
        conn.execute(
            'INSERT INTO sync_state '
            '(folder, watermark_date, last_polled_at, '
            'seen_message_ids) '
            'VALUES (?, ?, ?, ?) '
            'ON CONFLICT(folder) DO UPDATE SET '
            'watermark_date=COALESCE(excluded.watermark_date, '
            'watermark_date), '
            'last_polled_at=COALESCE(excluded.last_polled_at, '
            'last_polled_at), '
            'seen_message_ids=COALESCE('
            'excluded.seen_message_ids, seen_message_ids)',
            (folder, watermark_date, last_polled_at, seen_json),
        )
        conn.commit()
    finally:
        conn.close()


def search_emails(
    account_id: str,
    since: str,
    keywords: list[str],
    folders: list[str] | None = None,
) -> list[dict]:
    """Search emails by date + keywords for waiting-task checker.

    Returns matching rows as dicts.
    """
    path = db_path_for_account(account_id)
    if not path.exists():
        return []

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        clauses = ['date >= ?']
        params: list[str] = [since]

        if folders:
            placeholders = ','.join('?' for _ in folders)
            clauses.append(f'folder IN ({placeholders})')
            params.extend(folders)

        kw_parts = []
        for kw in keywords:
            kw_parts.append('(subject LIKE ? OR body_text LIKE ?)')
            params.append(f'%{kw}%')
            params.append(f'%{kw}%')
        if kw_parts:
            clauses.append(f'({" OR ".join(kw_parts)})')

        where = ' AND '.join(clauses)
        sql = 'SELECT * FROM emails WHERE ' + where + ' ORDER BY date DESC'
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
