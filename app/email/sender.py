"""SMTP email sender.

Sends emails via SMTP and records them in the per-account
SQLite DB so they are immediately visible to agents.
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
    """
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
