"""Tests for app.email.db — per-account SQLite email storage."""

from datetime import datetime
import json
import sqlite3

import pytest

from app.email.db import (
    db_path_for_account,
    get_new_emails_since,
    get_sync_state,
    init_email_db,
    search_emails,
    store_emails,
    update_sync_state,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _use_tmp_email_dir(tmp_path, monkeypatch):
    """Redirect EMAIL_DBS_DIR to a temp directory."""
    monkeypatch.setattr('app.email.db.EMAIL_DBS_DIR', tmp_path)


@pytest.fixture()
def account_id():
    return 'test-account-001'


@pytest.fixture()
def initialized_db(account_id):
    """Init and return the DB path."""
    return init_email_db(account_id)


class TestInitEmailDb:
    def test_creates_tables_and_wal(self, initialized_db):
        conn = sqlite3.connect(str(initialized_db))
        # WAL mode
        mode = conn.execute('PRAGMA journal_mode').fetchone()[0]
        assert mode == 'wal'

        # Tables exist
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert 'emails' in tables
        assert 'sync_state' in tables

        # Indexes exist
        indexes = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert 'idx_emails_sender' in indexes
        assert 'idx_emails_date' in indexes
        assert 'idx_emails_folder' in indexes
        assert 'idx_emails_recv' in indexes

        # received_epoch column exists (epoch axis for the watermark).
        cols = {r[1] for r in conn.execute('PRAGMA table_info(emails)')}
        assert 'received_epoch' in cols
        conn.close()

    def test_idempotent(self, account_id):
        p1 = init_email_db(account_id)
        p2 = init_email_db(account_id)
        assert p1 == p2


class TestStoreEmails:
    def test_insert_and_count(self, account_id, initialized_db):
        emails = [
            {
                'message_id': '<msg1@example.com>',
                'folder': 'INBOX',
                'subject': 'Hello',
                'sender': 'alice@example.com',
                'recipient': 'bob@example.com',
                'date': '2025-01-01T00:00:00',
                'body_text': 'Hi there',
                'email_account': 'bob@example.com',
            },
            {
                'message_id': '<msg2@example.com>',
                'folder': 'INBOX',
                'subject': 'World',
                'sender': 'carol@example.com',
                'date': '2025-01-02T00:00:00',
                'body_text': 'Hey',
                'email_account': 'bob@example.com',
            },
        ]
        count = store_emails(account_id, emails)
        assert count == 2

    def test_duplicate_ignored(self, account_id, initialized_db):
        em = [
            {
                'message_id': '<dup@example.com>',
                'folder': 'INBOX',
                'subject': 'Dup',
                'date': '2025-01-01T00:00:00',
                'email_account': 'bob@example.com',
            }
        ]
        assert store_emails(account_id, em) == 1
        assert store_emails(account_id, em) == 0

    def test_same_message_id_different_folders(
        self, account_id, initialized_db
    ):
        em_inbox = [
            {
                'message_id': '<cross@example.com>',
                'folder': 'INBOX',
                'subject': 'Cross',
                'date': '2025-01-01T00:00:00',
            }
        ]
        em_sent = [
            {
                'message_id': '<cross@example.com>',
                'folder': 'Sent',
                'subject': 'Cross',
                'date': '2025-01-01T00:00:00',
            }
        ]
        assert store_emails(account_id, em_inbox) == 1
        assert store_emails(account_id, em_sent) == 1

    def test_empty_list(self, account_id, initialized_db):
        assert store_emails(account_id, []) == 0

    def test_attachment_metadata_stored(self, account_id, initialized_db):
        att_json = json.dumps([
            {
                'filename': 'doc.pdf',
                'content_type': 'application/pdf',
                'size': 12345,
                'local_path': '/tmp/doc.pdf',
            }
        ])
        em = [
            {
                'message_id': '<att@example.com>',
                'folder': 'INBOX',
                'attachments': att_json,
                'date': '2025-01-01T00:00:00',
            }
        ]
        store_emails(account_id, em)
        conn = sqlite3.connect(str(db_path_for_account(account_id)))
        row = conn.execute(
            'SELECT attachments FROM emails '
            "WHERE message_id='<att@example.com>'"
        ).fetchone()
        conn.close()
        assert row is not None
        parsed = json.loads(row[0])
        assert parsed[0]['filename'] == 'doc.pdf'


class TestReceivedEpochWatermark:
    """The epoch axis that makes the watermark filter type-safe.

    Regression guard for the e2e watermark leak: the agent filtered
    ``WHERE date > <epoch>`` on the TEXT ``date`` column, and SQLite
    treats every TEXT value as greater than any INTEGER, so the filter
    matched every row and re-leaked an already-processed email. The
    fix is a real INTEGER ``received_epoch`` column; these tests pin
    both that it works AND why ``date`` cannot be used for it.
    """

    OLD = '2025-01-01T00:00:00+00:00'
    NEW = '2025-06-01T00:00:00+00:00'

    def _seed(self, account_id):
        store_emails(
            account_id,
            [
                {
                    'message_id': '<old@ex.com>',
                    'folder': 'INBOX',
                    'subject': 'old',
                    'date': self.OLD,
                    'fetched_at': self.OLD,
                    'body_text': 'SECRET_OLD',
                },
                {
                    'message_id': '<new@ex.com>',
                    'folder': 'INBOX',
                    'subject': 'new',
                    'date': self.NEW,
                    'fetched_at': self.NEW,
                    'body_text': 'SECRET_NEW',
                },
            ],
        )

    def test_received_epoch_populated(self, account_id, initialized_db):
        self._seed(account_id)
        conn = sqlite3.connect(str(db_path_for_account(account_id)))
        rows = dict(
            conn.execute(
                'SELECT message_id, received_epoch FROM emails'
            ).fetchall()
        )
        conn.close()
        assert rows['<old@ex.com>'] == int(
            datetime.fromisoformat(self.OLD).timestamp()
        )
        assert rows['<new@ex.com>'] == int(
            datetime.fromisoformat(self.NEW).timestamp()
        )

    def test_get_new_emails_since_excludes_pre_watermark(
        self, account_id, initialized_db
    ):
        self._seed(account_id)
        cursor = int(datetime.fromisoformat(self.OLD).timestamp())
        emails, max_epoch = get_new_emails_since(account_id, cursor)
        ids = {e['message_id'] for e in emails}
        assert ids == {'<new@ex.com>'}  # old one (== cursor) excluded
        assert max_epoch == int(datetime.fromisoformat(self.NEW).timestamp())

    def test_date_column_is_the_footgun(self, account_id, initialized_db):
        """`WHERE date > <epoch>` matches EVERY row (the leak); the same
        comparison on received_epoch filters correctly."""
        self._seed(account_id)
        cursor = int(datetime.fromisoformat(self.OLD).timestamp())
        conn = sqlite3.connect(str(db_path_for_account(account_id)))
        try:
            leaked = conn.execute(
                'SELECT COUNT(*) FROM emails WHERE date > ?', (cursor,)
            ).fetchone()[0]
            correct = conn.execute(
                'SELECT COUNT(*) FROM emails WHERE received_epoch > ?',
                (cursor,),
            ).fetchone()[0]
        finally:
            conn.close()
        assert leaked == 2  # TEXT > INTEGER is always true → leaks all
        assert correct == 1  # integer axis excludes the pre-watermark row

    def test_migration_backfills_legacy_null(self, account_id, initialized_db):
        """A row with received_epoch NULL (as in a pre-migration DB) is
        backfilled by init_email_db and then filters correctly."""
        self._seed(account_id)
        path = db_path_for_account(account_id)
        conn = sqlite3.connect(str(path))
        conn.execute('UPDATE emails SET received_epoch = NULL')
        conn.commit()
        conn.close()

        init_email_db(account_id)  # re-init backfills

        conn = sqlite3.connect(str(path))
        nulls = conn.execute(
            'SELECT COUNT(*) FROM emails WHERE received_epoch IS NULL'
        ).fetchone()[0]
        conn.close()
        assert nulls == 0

    def test_init_upgrades_db_missing_received_epoch_column(
        self, account_id, tmp_path
    ):
        """The real legacy case: an ``emails`` table created before the
        ``received_epoch`` column existed at all (column absent, not just
        NULL). ``init_email_db`` must add the column + index and backfill
        WITHOUT throwing.

        Regression for the production incident where every account's sync
        failed with ``sqlite3.OperationalError: no such column:
        received_epoch`` — the received_epoch index in the schema script
        ran against a legacy table before the migration could add the
        column, aborting the sync before any mail was fetched.
        """
        path = db_path_for_account(account_id)
        conn = sqlite3.connect(str(path))
        # Exactly the pre-received_epoch schema shipped to clients.
        conn.executescript(
            """
            CREATE TABLE emails (
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
            CREATE INDEX idx_emails_date ON emails(date);
            CREATE TABLE sync_state (
                folder TEXT PRIMARY KEY, watermark_date TEXT,
                last_polled_at TEXT, seen_message_ids TEXT
            );
            """
        )
        conn.execute(
            'INSERT INTO emails(message_id, folder, subject, fetched_at, '
            'date) VALUES(?,?,?,?,?)',
            ('<otp@ex.com>', 'INBOX', 'OTP', self.NEW, self.NEW),
        )
        conn.commit()
        conn.close()

        # Must not raise (the incident was an uncaught OperationalError).
        init_email_db(account_id)

        conn = sqlite3.connect(str(path))
        try:
            cols = {r[1] for r in conn.execute('PRAGMA table_info(emails)')}
            indexes = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                )
            }
            epoch = conn.execute(
                "SELECT received_epoch FROM emails WHERE message_id='<otp@ex.com>'"
            ).fetchone()[0]
        finally:
            conn.close()

        assert 'received_epoch' in cols  # column added
        assert 'idx_emails_recv' in indexes  # index created after column
        # Pre-existing row backfilled onto the epoch axis.
        assert epoch == int(datetime.fromisoformat(self.NEW).timestamp())

        # And the watermark filter now works on the upgraded DB.
        emails, _ = get_new_emails_since(account_id, 0)
        assert {e['message_id'] for e in emails} == {'<otp@ex.com>'}


class TestSyncState:
    def test_round_trip(self, account_id, initialized_db):
        update_sync_state(
            account_id,
            'INBOX',
            watermark_date='2025-01-15T00:00:00',
            last_polled_at='2025-01-15T12:00:00',
            seen_message_ids=['<a>', '<b>'],
        )
        state = get_sync_state(account_id, 'INBOX')
        assert state['watermark_date'] == '2025-01-15T00:00:00'
        assert state['last_polled_at'] == '2025-01-15T12:00:00'
        assert set(state['seen_message_ids']) == {'<a>', '<b>'}

    def test_empty_state(self, account_id, initialized_db):
        state = get_sync_state(account_id, 'INBOX')
        assert state == {}

    def test_upsert(self, account_id, initialized_db):
        update_sync_state(
            account_id,
            'INBOX',
            watermark_date='2025-01-01T00:00:00',
        )
        update_sync_state(
            account_id,
            'INBOX',
            watermark_date='2025-02-01T00:00:00',
        )
        state = get_sync_state(account_id, 'INBOX')
        assert state['watermark_date'] == '2025-02-01T00:00:00'


class TestSearchEmails:
    def test_search_by_keyword_and_date(self, account_id, initialized_db):
        emails = [
            {
                'message_id': '<s1@ex.com>',
                'folder': 'INBOX',
                'subject': 'Order shipped',
                'body_text': 'Your order has shipped',
                'date': '2025-03-01T00:00:00',
            },
            {
                'message_id': '<s2@ex.com>',
                'folder': 'INBOX',
                'subject': 'Invoice',
                'body_text': 'Please pay invoice',
                'date': '2025-03-02T00:00:00',
            },
            {
                'message_id': '<s3@ex.com>',
                'folder': 'INBOX',
                'subject': 'Old email',
                'body_text': 'shipped long ago',
                'date': '2024-01-01T00:00:00',
            },
        ]
        store_emails(account_id, emails)

        results = search_emails(
            account_id,
            since='2025-01-01T00:00:00',
            keywords=['shipped'],
        )
        assert len(results) == 1
        assert results[0]['message_id'] == '<s1@ex.com>'

    def test_search_in_body(self, account_id, initialized_db):
        emails = [
            {
                'message_id': '<b1@ex.com>',
                'folder': 'INBOX',
                'subject': 'Hello',
                'body_text': 'The tracking number is ready',
                'date': '2025-03-01T00:00:00',
            },
        ]
        store_emails(account_id, emails)
        results = search_emails(
            account_id,
            since='2025-01-01T00:00:00',
            keywords=['tracking'],
        )
        assert len(results) == 1

    def test_search_folder_filter(self, account_id, initialized_db):
        emails = [
            {
                'message_id': '<f1@ex.com>',
                'folder': 'INBOX',
                'subject': 'match',
                'date': '2025-03-01T00:00:00',
            },
            {
                'message_id': '<f2@ex.com>',
                'folder': 'Sent',
                'subject': 'match',
                'date': '2025-03-01T00:00:00',
            },
        ]
        store_emails(account_id, emails)
        results = search_emails(
            account_id,
            since='2025-01-01T00:00:00',
            keywords=['match'],
            folders=['INBOX'],
        )
        assert len(results) == 1
        assert results[0]['folder'] == 'INBOX'

    def test_no_db_file(self, tmp_path, account_id):
        results = search_emails(
            account_id,
            since='2025-01-01T00:00:00',
            keywords=['any'],
        )
        assert results == []
