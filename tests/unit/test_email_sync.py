"""Tests for email sync flow (app.channels.email_poller).

Uses mock IMAP to test sync_account_emails() and related helpers.
"""

from datetime import UTC, datetime
import email.mime.multipart
import email.mime.text
import imaplib
from unittest.mock import MagicMock, patch

import pytest

from app.channels.email_poller import sync_account_emails
from app.email.db import (
    get_sync_state,
    search_emails,
)

pytestmark = pytest.mark.unit


def _make_rfc822(
    subject='Test',
    sender='alice@example.com',
    to='bob@example.com',
    body='Hello',
    html=None,
    message_id=None,
    date=None,
    attachment_name=None,
    attachment_data=None,
    html_only=False,
):
    """Build realistic email bytes using email.mime."""
    if html_only and html:
        # HTML-only payload with no text/plain part — mirrors how
        # Amazon Seller Central notifications ship.
        msg = email.mime.text.MIMEText(html, 'html')
    elif html or attachment_name:
        msg = email.mime.multipart.MIMEMultipart('mixed')
        msg.attach(email.mime.text.MIMEText(body, 'plain'))
        if html:
            msg.attach(email.mime.text.MIMEText(html, 'html'))
        if attachment_name and attachment_data:
            att = email.mime.text.MIMEText(attachment_data, 'plain')
            att.add_header(
                'Content-Disposition',
                'attachment',
                filename=attachment_name,
            )
            msg.attach(att)
    else:
        msg = email.mime.text.MIMEText(body, 'plain')

    msg['Subject'] = subject
    msg['From'] = sender
    msg['To'] = to
    msg['Message-ID'] = message_id or f'<{subject.replace(" ", "")}@test>'
    msg['Date'] = date or datetime.now(UTC).isoformat()
    return msg.as_bytes()


class MockIMAP:
    """Mock IMAP4_SSL for testing."""

    def __init__(self, emails=None, folders=None):
        self._emails = emails or {}  # folder -> [bytes]
        self._folders = folders or [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasNoChildren \\Sent) "/" "Sent"',
        ]
        self._current_folder = 'INBOX'

    def login(self, user, password):
        return ('OK', [b'Logged in'])

    def logout(self):
        return ('BYE', [])

    def list(self):
        return ('OK', self._folders)

    def select(self, folder='INBOX', readonly=False):
        self._current_folder = folder
        msgs = self._emails.get(folder, [])
        return ('OK', [str(len(msgs)).encode()])

    def search(self, charset, criteria):
        msgs = self._emails.get(self._current_folder, [])
        if not msgs:
            return ('OK', [b''])
        nums = ' '.join(str(i + 1) for i in range(len(msgs)))
        return ('OK', [nums.encode()])

    def fetch(self, num, parts):
        idx = int(num) - 1
        msgs = self._emails.get(self._current_folder, [])
        if idx < 0 or idx >= len(msgs):
            return ('NO', [])
        raw = msgs[idx]
        return ('OK', [(b'1 (RFC822 {%d}' % len(raw), raw), b')'])

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _use_tmp_email_dir(tmp_path, monkeypatch):
    """Redirect EMAIL_DBS_DIR to a temp directory."""
    monkeypatch.setattr('app.email.db.EMAIL_DBS_DIR', tmp_path)
    monkeypatch.setattr(
        'app.channels.email_poller.attachments_dir_for_account',
        lambda aid: tmp_path / f'{aid}_att',
    )
    (tmp_path / 'test_att').mkdir(exist_ok=True)


@pytest.fixture()
def mock_account():
    """Create a mock EmailAccount object."""
    acct = MagicMock()
    acct.id = 'acct-001'
    acct.email = 'user@example.com'
    acct.imap_host = 'imap.example.com'
    acct.imap_port = 993
    acct.use_ssl = True
    return acct


class TestSyncAccountEmails:
    @pytest.mark.asyncio
    async def test_stores_emails_in_db(self, mock_account, tmp_path):
        raw1 = _make_rfc822(
            subject='Order Shipped',
            message_id='<msg1@test>',
        )
        raw2 = _make_rfc822(
            subject='Invoice Ready',
            message_id='<msg2@test>',
        )
        mock_imap = MockIMAP(emails={'INBOX': [raw1, raw2], 'Sent': []})

        with patch('imaplib.IMAP4_SSL', return_value=mock_imap):
            count = await sync_account_emails(mock_account, 'password123')

        assert count == 2
        # Verify in DB
        results = search_emails(
            'acct-001',
            since='2020-01-01T00:00:00',
            keywords=['Order'],
        )
        assert len(results) == 1
        assert 'Order Shipped' in results[0]['subject']

    @pytest.mark.asyncio
    async def test_incremental_sync(self, mock_account, tmp_path):
        raw1 = _make_rfc822(subject='First', message_id='<first@test>')
        mock_imap = MockIMAP(emails={'INBOX': [raw1], 'Sent': []})

        with patch('imaplib.IMAP4_SSL', return_value=mock_imap):
            c1 = await sync_account_emails(mock_account, 'pw')

        assert c1 == 1

        # Second sync with same email — should be 0 new
        mock_imap2 = MockIMAP(emails={'INBOX': [raw1], 'Sent': []})
        with patch('imaplib.IMAP4_SSL', return_value=mock_imap2):
            c2 = await sync_account_emails(mock_account, 'pw')

        assert c2 == 0

    @pytest.mark.asyncio
    async def test_sent_folder_detection(self, mock_account, tmp_path):
        raw_inbox = _make_rfc822(
            subject='Inbox Email', message_id='<inbox@test>'
        )
        raw_sent = _make_rfc822(subject='Sent Email', message_id='<sent@test>')
        mock_imap = MockIMAP(
            emails={
                'INBOX': [raw_inbox],
                'Sent': [raw_sent],
            },
            folders=[
                b'(\\HasNoChildren) "/" "INBOX"',
                b'(\\HasNoChildren \\Sent) "/" "Sent"',
            ],
        )

        with patch('imaplib.IMAP4_SSL', return_value=mock_imap):
            count = await sync_account_emails(mock_account, 'pw')

        assert count == 2
        # Both INBOX and Sent
        results = search_emails(
            'acct-001',
            since='2020-01-01T00:00:00',
            keywords=['Email'],
        )
        folders = {r['folder'] for r in results}
        assert 'INBOX' in folders
        assert 'Sent' in folders

    @pytest.mark.asyncio
    async def test_html_body_extraction(self, mock_account, tmp_path):
        raw = _make_rfc822(
            subject='HTML Test',
            body='Plain text',
            html='<h1>Rich</h1>',
            message_id='<html@test>',
        )
        mock_imap = MockIMAP(emails={'INBOX': [raw], 'Sent': []})

        with patch('imaplib.IMAP4_SSL', return_value=mock_imap):
            await sync_account_emails(mock_account, 'pw')

        results = search_emails(
            'acct-001',
            since='2020-01-01T00:00:00',
            keywords=['HTML'],
        )
        assert len(results) == 1
        assert results[0]['body_html'] is not None
        assert '<h1>Rich</h1>' in results[0]['body_html']

    @pytest.mark.asyncio
    async def test_html_nbsp_normalized_to_ascii_space(
        self, mock_account, tmp_path
    ):
        """`&nbsp;` in Amazon notifications must not leave U+00A0
        NBSPs in `body_text`. SQL LIKE keyword searches with regular
        spaces would miss rows that sit next to those entities.
        """
        html = (
            '<html><body><p>ASIN B0&nbsp;123&nbsp;456 requires '
            'action by&nbsp;2026-05-16.</p></body></html>'
        )
        raw = _make_rfc822(
            subject='NBSP normalize test',
            html=html,
            html_only=True,
            message_id='<nbsp@test>',
        )
        mock_imap = MockIMAP(emails={'INBOX': [raw], 'Sent': []})
        with patch('imaplib.IMAP4_SSL', return_value=mock_imap):
            await sync_account_emails(mock_account, 'pw')

        # Search by a keyword that spans former `&nbsp;` positions
        # with ASCII spaces — must find the row.
        results = search_emails(
            'acct-001',
            since='2020-01-01T00:00:00',
            keywords=['B0 123 456'],
        )
        assert len(results) == 1
        body_text = results[0]['body_text'] or ''
        assert '\u00a0' not in body_text

    @pytest.mark.asyncio
    async def test_html_only_falls_back_to_stripped_text(
        self, mock_account, tmp_path
    ):
        """Amazon-style HTML-only emails must leave body_text populated.

        Regression guard: when an email ships only text/html (no
        text/plain part), body_text was ''. Agents that keyword-search
        via `body_text LIKE ...` silently missed every such email.
        """
        html = (
            '<html><head><style>.x{color:red}</style></head>'
            '<body><h1>Action Required</h1>'
            '<p>You must remove FBA inventory by '
            '<strong>2026-05-16</strong>.</p>'
            '<script>tracker()</script>'
            '</body></html>'
        )
        raw = _make_rfc822(
            subject='Amazon stranded inventory',
            html=html,
            html_only=True,
            message_id='<amz@test>',
        )
        mock_imap = MockIMAP(emails={'INBOX': [raw], 'Sent': []})

        with patch('imaplib.IMAP4_SSL', return_value=mock_imap):
            await sync_account_emails(mock_account, 'pw')

        results = search_emails(
            'acct-001',
            since='2020-01-01T00:00:00',
            keywords=['stranded'],
        )
        assert len(results) == 1
        row = results[0]
        body_text = row['body_text'] or ''
        assert body_text, 'body_text should be populated from HTML fallback'
        # Scripts / styles / tags are stripped, content survives.
        assert '2026-05-16' in body_text
        assert 'Action Required' in body_text
        assert 'tracker()' not in body_text
        assert '<' not in body_text
        # Raw HTML still available for callers that want it.
        assert row['body_html'] and '<h1>' in row['body_html']

    @pytest.mark.asyncio
    async def test_watermark_advances(self, mock_account, tmp_path):
        raw = _make_rfc822(subject='WM Test', message_id='<wm@test>')
        mock_imap = MockIMAP(emails={'INBOX': [raw], 'Sent': []})

        with patch('imaplib.IMAP4_SSL', return_value=mock_imap):
            await sync_account_emails(mock_account, 'pw')

        state = get_sync_state('acct-001', 'INBOX')
        assert state.get('watermark_date') is not None
        assert state.get('last_polled_at') is not None
        assert '<wm@test>' in state.get('seen_message_ids', [])

    @pytest.mark.asyncio
    async def test_raises_on_imap_login_error(self, mock_account, tmp_path):
        """IMAP login failure propagates to caller."""
        mock_imap = MockIMAP()
        mock_imap.login = MagicMock(
            side_effect=imaplib.IMAP4.error('auth failed')
        )
        mock_imap.logout = MagicMock(return_value=('BYE', []))

        with patch('imaplib.IMAP4_SSL', return_value=mock_imap):
            with pytest.raises(imaplib.IMAP4.error, match='auth'):
                await sync_account_emails(mock_account, 'bad')

        # logout is still called via finally
        mock_imap.logout.assert_called_once()

    @pytest.mark.asyncio
    async def test_logout_called_on_folder_error(self, mock_account, tmp_path):
        """IMAP logout runs even when folder sync raises."""
        mock_imap = MockIMAP()
        mock_imap.select = MagicMock(side_effect=Exception('folder error'))
        mock_imap.logout = MagicMock(return_value=('BYE', []))

        with patch('imaplib.IMAP4_SSL', return_value=mock_imap):
            with pytest.raises(Exception, match='folder error'):
                await sync_account_emails(mock_account, 'pw')

        mock_imap.logout.assert_called_once()
