"""SMTP email sender.

Sends emails via SMTP and records them in the per-account
SQLite DB so they are immediately visible to agents.

THE INVARIANT: no email leaves a mailbox whose owner has turned
``EmailAccount.allow_send`` off. Sending is on by default (the owner's
call: on by default changes nothing for existing users; off would); the
user turns it off per mailbox. The check lives HERE, in the module that
opens the SMTP connection, not in the routes that call it: every send
path (the REST endpoint, the in-task MCP tool that POSTs to it, the
external ``/api/mcp`` toolset that calls the route in-process) funnels
through ``send_email``, so a caller added later is refused without
having to remember to check.
"""

import asyncio
from datetime import UTC, datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import logging
import os
import smtplib
import uuid

from app.email.db import init_email_db, store_emails
from app.models.email_account import EmailAccount

logger = logging.getLogger(__name__)

# Where the user turns sending back on. Kept next to the gate so the
# refusal can never point at a place the UI no longer has.
SEND_PERMISSION_LOCATION = (
    'Settings → Email, switch "Let the AI send email from this '
    'mailbox" on that mailbox'
)


class SendNotAllowedError(Exception):
    """The user has turned sending off for this mailbox.

    The message is written for the agent that attempted the send: what
    happened, what the user can do, and what the agent can still do.
    """

    def __init__(self, email: str):
        self.email = email
        super().__init__(
            f'The user has turned sending off for {email}, so nothing was '
            'sent. Do not retry. Tell the user they can turn it back on in '
            f'{SEND_PERMISSION_LOCATION}. You can still draft the email '
            'and show it to the user.'
        )


def ensure_send_allowed(account: EmailAccount) -> None:
    """Raise :class:`SendNotAllowedError` if the user turned sending off.

    Only an explicit off (``False``, or a falsy stored value such as
    ``0``) refuses. ``None`` — "never decided" — is allowed, because it
    can only mean the default and the default is on: the column is
    ``NOT NULL DEFAULT 1``, so a stored row is never NULL, and ``None``
    appears only on an object that has not been flushed yet, whose
    column default is likewise on. The refusal must track the user's
    decision, and the only way to record "off" is the user switching it
    off (``PUT …/send-permission``, interactive-only).
    """
    if account.allow_send is not None and not account.allow_send:
        raise SendNotAllowedError(account.email)


async def send_email(
    account: EmailAccount,
    password: str,
    to: str | list[str],
    subject: str,
    body: str,
    body_html: str | None = None,
    attachments: list[str] | None = None,
) -> dict:
    """Send an email via SMTP.

    Returns ``{'ok': True, 'message_id': ...}`` on success,
    ``{'ok': False, 'error': ...}`` on failure.

    Raises:
        SendNotAllowedError: the account's ``allow_send`` is off. Raised
            before any SMTP connection is opened.
    """
    ensure_send_allowed(account)
    recipients = [to] if isinstance(to, str) else list(to)

    def _send_sync() -> dict:
        return _do_send(
            account,
            password,
            recipients,
            subject,
            body,
            body_html,
            attachments,
        )

    return await asyncio.to_thread(_send_sync)


def _do_send(
    account: EmailAccount,
    password: str,
    recipients: list[str],
    subject: str,
    body: str,
    body_html: str | None,
    attachments: list[str] | None,
) -> dict:
    """Synchronous SMTP send."""
    # Re-checked here: this is the function that opens the socket, so
    # a future caller that reaches it directly is still refused.
    ensure_send_allowed(account)
    smtp_host = account.smtp_host
    smtp_port = account.smtp_port or 465
    use_tls = account.smtp_use_tls

    if not smtp_host:
        return {
            'ok': False,
            'error': 'No SMTP host configured for this account',
        }

    # Build message
    msg = MIMEMultipart('mixed')
    msg['From'] = account.email
    msg['To'] = ', '.join(recipients)
    msg['Subject'] = subject
    message_id = f'<{uuid.uuid4()}@vibe-seller>'
    msg['Message-ID'] = message_id
    msg['Date'] = datetime.now(UTC).isoformat()

    # Body
    if body_html:
        alt = MIMEMultipart('alternative')
        alt.attach(MIMEText(body, 'plain', 'utf-8'))
        alt.attach(MIMEText(body_html, 'html', 'utf-8'))
        msg.attach(alt)
    else:
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

    # Attachments
    if attachments:
        for filepath in attachments:
            if not os.path.isfile(filepath):
                continue
            filename = os.path.basename(filepath)
            with open(filepath, 'rb') as f:
                att = MIMEApplication(f.read())
            att.add_header(
                'Content-Disposition',
                'attachment',
                filename=filename,
            )
            msg.attach(att)

    try:
        if use_tls:
            # STARTTLS (typically port 587)
            server = smtplib.SMTP(smtp_host, smtp_port)
            server.ehlo()
            server.starttls()
            server.ehlo()
        else:
            # SMTP_SSL (typically port 465)
            server = smtplib.SMTP_SSL(smtp_host, smtp_port)

        server.login(account.email, password)
        server.sendmail(account.email, recipients, msg.as_string())
        server.quit()
    except smtplib.SMTPAuthenticationError as e:
        return {
            'ok': False,
            'error': f'SMTP authentication failed: {e}',
        }
    except Exception as e:
        return {'ok': False, 'error': f'SMTP error: {e}'}

    # Record sent email in local DB immediately
    try:
        init_email_db(account.id)
        store_emails(
            account.id,
            [
                {
                    'message_id': message_id,
                    'folder': 'Sent',
                    'subject': subject,
                    'sender': account.email,
                    'recipient': ', '.join(recipients),
                    'date': datetime.now(UTC).isoformat(),
                    'body_text': body,
                    'body_html': body_html,
                    'email_account': account.email,
                    'fetched_at': datetime.now(UTC).isoformat(),
                }
            ],
        )
    except Exception:
        logger.exception('Failed to record sent email in local DB')

    return {'ok': True, 'message_id': message_id}
