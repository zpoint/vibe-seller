"""Tests for app.email.sender — SMTP email sending."""

import smtplib as _smtplib
from unittest.mock import MagicMock, patch

import pytest

from app.email.db import init_email_db, search_emails
from app.email.sender import (
    SendNotAllowedError,
    _do_send,  # noqa: PLC2701 - the socket-opening layer is the gate
    ensure_send_allowed,
    send_email,
)

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
    acct.allow_send = True
    return acct


@pytest.fixture()
def mock_account_starttls():
    acct = MagicMock()
    acct.id = 'sender-002'
    acct.email = 'sender@gmail.com'
    acct.smtp_host = 'smtp.gmail.com'
    acct.smtp_port = 587
    acct.smtp_use_tls = True
    acct.allow_send = True
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
        acct.allow_send = True

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


class TestSendPermission:
    """``allow_send`` is the gate, and it sits before any SMTP."""

    @pytest.mark.parametrize(
        'value',
        # False is the column default; None and a bare MagicMock stand
        # for "unset / not a real bool" — all must refuse (default deny).
        [False, None, 'unset'],
    )
    @pytest.mark.asyncio
    async def test_refused_before_any_smtp(self, mock_account, value):
        mock_account.allow_send = MagicMock() if value == 'unset' else value
        with (
            patch('smtplib.SMTP_SSL') as ssl_cls,
            patch('smtplib.SMTP') as plain_cls,
        ):
            with pytest.raises(SendNotAllowedError) as exc_info:
                await send_email(
                    account=mock_account,
                    password='pw',
                    to='buyer@example.com',
                    subject='Refused',
                    body='x',
                )
        ssl_cls.assert_not_called()
        plain_cls.assert_not_called()
        message = str(exc_info.value)
        assert 'sender@example.com' in message
        assert 'Settings → Email' in message
        assert 'draft' in message

    def test_sync_sender_refuses_too(self, mock_account):
        """The function that opens the socket re-checks, so a caller
        that reaches it without ``send_email`` is refused as well."""
        mock_account.allow_send = False
        with patch('smtplib.SMTP_SSL') as ssl_cls:
            with pytest.raises(SendNotAllowedError):
                _do_send(mock_account, 'pw', ['b@x.com'], 's', 'b', None, None)
        ssl_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_refused_send_is_not_recorded_as_sent(self, mock_account):
        init_email_db(mock_account.id)
        mock_account.allow_send = False
        with pytest.raises(SendNotAllowedError):
            await send_email(
                account=mock_account,
                password='pw',
                to='buyer@example.com',
                subject='Never sent',
                body='x',
            )
        assert (
            search_emails(
                'sender-001',
                since='2020-01-01T00:00:00',
                keywords=['Never sent'],
            )
            == []
        )

    def test_allowed_is_a_no_op(self, mock_account):
        ensure_send_allowed(mock_account)  # allow_send=True: no raise
