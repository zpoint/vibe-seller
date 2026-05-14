"""Tests for app.email.sender — SMTP email sending."""

import smtplib as _smtplib
from unittest.mock import MagicMock, patch

import pytest

from app.email.db import init_email_db, search_emails
from app.email.sender import send_email

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _use_tmp_email_dir(tmp_path, monkeypatch):
    """Redirect EMAIL_DBS_DIR to a temp directory."""
    monkeypatch.setattr('app.email.db.EMAIL_DBS_DIR', tmp_path)


@pytest.fixture()
def mock_account():
    acct = MagicMock()
    acct.id = 'sender-001'
    acct.email = 'sender@example.com'
    acct.smtp_host = 'smtp.example.com'
    acct.smtp_port = 465
    acct.smtp_use_tls = False
    return acct


@pytest.fixture()
def mock_account_starttls():
    acct = MagicMock()
    acct.id = 'sender-002'
    acct.email = 'sender@gmail.com'
    acct.smtp_host = 'smtp.gmail.com'
    acct.smtp_port = 587
    acct.smtp_use_tls = True
    return acct


class TestSendEmail:
    @pytest.mark.asyncio
    async def test_send_ssl(self, mock_account):
        init_email_db(mock_account.id)
        mock_smtp = MagicMock()
        with patch('smtplib.SMTP_SSL', return_value=mock_smtp):
            result = await send_email(
                account=mock_account,
                password='pw123',
                to='recipient@example.com',
                subject='Test Subject',
                body='Hello world',
            )

        assert result['ok'] is True
        assert 'message_id' in result
        mock_smtp.login.assert_called_once_with('sender@example.com', 'pw123')
        mock_smtp.sendmail.assert_called_once()
        mock_smtp.quit.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_starttls(self, mock_account_starttls):
        init_email_db(mock_account_starttls.id)
        mock_smtp = MagicMock()
        with patch('smtplib.SMTP', return_value=mock_smtp):
            result = await send_email(
                account=mock_account_starttls,
                password='pw123',
                to='recipient@example.com',
                subject='TLS Test',
                body='Hello',
            )

        assert result['ok'] is True
        mock_smtp.starttls.assert_called_once()

    @pytest.mark.asyncio
    async def test_sent_email_in_local_db(self, mock_account):
        init_email_db(mock_account.id)
        mock_smtp = MagicMock()
        with patch('smtplib.SMTP_SSL', return_value=mock_smtp):
            await send_email(
                account=mock_account,
                password='pw',
                to='bob@example.com',
                subject='DB Check',
                body='Should appear in DB',
            )

        results = search_emails(
            'sender-001',
            since='2020-01-01T00:00:00',
            keywords=['DB Check'],
        )
        assert len(results) == 1
        assert results[0]['folder'] == 'Sent'
        assert results[0]['recipient'] == 'bob@example.com'

    @pytest.mark.asyncio
    async def test_auth_failure(self, mock_account):
        mock_smtp = MagicMock()
        mock_smtp.login.side_effect = _smtplib.SMTPAuthenticationError(
            535, b'Auth failed'
        )
        with patch('smtplib.SMTP_SSL', return_value=mock_smtp):
            result = await send_email(
                account=mock_account,
                password='bad',
                to='x@x.com',
                subject='Fail',
                body='x',
            )

        assert result['ok'] is False
        assert 'authentication' in result['error'].lower()

    @pytest.mark.asyncio
    async def test_no_smtp_host(self):
        acct = MagicMock()
        acct.id = 'no-smtp'
        acct.email = 'x@x.com'
        acct.smtp_host = None
        acct.smtp_port = None

        result = await send_email(
            account=acct,
            password='pw',
            to='y@y.com',
            subject='No SMTP',
            body='x',
        )
        assert result['ok'] is False
        assert 'No SMTP host' in result['error']

    @pytest.mark.asyncio
    async def test_send_with_html(self, mock_account):
        init_email_db(mock_account.id)
        mock_smtp = MagicMock()
        with patch('smtplib.SMTP_SSL', return_value=mock_smtp):
            result = await send_email(
                account=mock_account,
                password='pw',
                to='bob@example.com',
                subject='HTML Email',
                body='Plain',
                body_html='<h1>Rich</h1>',
            )

        assert result['ok'] is True
        # Verify the MIME includes HTML
        call_args = mock_smtp.sendmail.call_args
        msg_str = call_args[0][2]
        assert 'text/html' in msg_str

    @pytest.mark.asyncio
    async def test_send_to_multiple(self, mock_account):
        init_email_db(mock_account.id)
        mock_smtp = MagicMock()
        with patch('smtplib.SMTP_SSL', return_value=mock_smtp):
            result = await send_email(
                account=mock_account,
                password='pw',
                to=['a@x.com', 'b@x.com'],
                subject='Multi',
                body='Hi all',
            )

        assert result['ok'] is True
        call_args = mock_smtp.sendmail.call_args
        assert call_args[0][1] == ['a@x.com', 'b@x.com']
