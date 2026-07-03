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


def get_new_emails_since(
    account_id: str,
    since_epoch: int,
    folder: str = 'INBOX',
    limit: int = 200,
) -> tuple[list[dict], int]:
    """Return emails fetched after ``since_epoch`` + the max epoch.

    Server-side watermark filter for the scheduled email sweep. Agents
    must NOT hand-write a raw ``SELECT`` for this: an unfiltered query
    pulls already-processed email bodies into the agent's context and
    leaks them into the current run (see
    ``tests/e2e/test_email_watermark_e2e.py`` — the run-2
    ``SECRET_1 not in transcript`` assertion). Filtering here moves the
    cursor contract from prose into code, so the bug class cannot recur
    from the agent surface.

    The cursor axis is ``fetched_at`` (when *we* stored the row), NOT
    the sender-controlled ``date`` header. "New messages that have
    arrived since the last run" means arrived in *our* store, and
    ``fetched_at`` is the only column monotonic with that. Filtering by
    ``date`` silently drops any email whose ``Date`` header predates the
    last sweep's watermark — a late-delivered, backdated, or
    clock-skewed message, or anything the sync job backfills after
    downtime — even though it arrived *after* the cursor. It also made
    the watermark handoff hostage to the agent persisting the exact
    server-computed value: a model that fumbled the sweep tool and set
    the cursor to wall-clock ``now`` would skip every genuinely-new but
    past-dated email (the flake this test caught on the MiniMax
    provider). ``fetched_at`` makes the invariant hold for *any*
    watermark the agent persists. ``COALESCE(fetched_at, date)`` keeps
    pre-``fetched_at`` rows swept instead of dropped.

    Each row carries an ``epoch`` column
    (``CAST(strftime('%s', COALESCE(fetched_at, date)) AS INTEGER)``) so
    the caller never has to translate ISO → epoch (a known year-off
    footgun). The second tuple element is the max epoch among returned
    rows, or ``since_epoch`` when nothing is new — so the caller can
    persist a monotonic, never-regressing watermark.
    """
    path = db_path_for_account(account_id)
    if not path.exists():
        return [], since_epoch

    # Fetch-time cursor: filter + order + returned epoch all key off
    # COALESCE(fetched_at, date) so the axis is consistent end to end.
    epoch_expr = "CAST(strftime('%s', COALESCE(fetched_at, date)) AS INTEGER)"
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            'SELECT message_id, folder, subject, sender, recipient,'
            ' date, body_text,'
            f' {epoch_expr} AS epoch'
            ' FROM emails'
            ' WHERE folder = ?'
            f'   AND {epoch_expr} > ?'
            ' ORDER BY epoch ASC'
            ' LIMIT ?',
            (folder, since_epoch, limit),
        ).fetchall()
    finally:
        conn.close()

    emails = [dict(r) for r in rows]
    max_epoch = since_epoch
    for em in emails:
        ep = em.get('epoch')
        if ep is not None and ep > max_epoch:
            max_epoch = ep
    return emails, max_epoch


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
