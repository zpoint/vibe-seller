"""What a task's prompt says about email: its store's mailboxes, or none.

Split out of ``app/task_runner_context.py`` (the repo's 800-line cap).
The section is always present for a store task: with mailboxes it says
how to read and send; without any it says plainly that this task has no
mail (``app.email.NO_STORE_MAILBOX``) — silence, with the email tools
still offered, left an agent hunting the machine for a way to send.
"""

from collections.abc import Sequence
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.email import NO_STORE_MAILBOX
from app.models.email_account import EmailAccount
from app.models.store_email_link import StoreEmailLink


class Mailbox(NamedTuple):
    """A linked email account as the prompt sees it."""

    email: str
    allow_send: bool = True


def send_policy_note(mailboxes: Sequence[Mailbox]) -> str:
    """Which mailboxes the agent may send from, in one line.

    Advisory only — the server refuses a send from any other mailbox
    (``app.email.sender.ensure_send_allowed``). Telling the agent up
    front just saves it a refused call.
    """
    sendable = [m.email for m in mailboxes if m.allow_send]
    blocked = [m.email for m in mailboxes if not m.allow_send]
    if not blocked:
        return 'Sending is allowed from: ' + ', '.join(sendable) + '.'
    note = (
        'Read/draft only (sending turned off by the user): '
        + ', '.join(blocked)
        + '. Do not call vibe_seller_send_email for these — it is '
        'refused. Draft the email and show it to the user instead, and '
        'tell them they can turn it back on with that mailbox\'s "Email '
        'sending permission" switch in Settings → Email.'
    )
    if sendable:
        note = 'Sending is allowed from: ' + ', '.join(sendable) + '. ' + note
    return note


async def get_store_emails(db: AsyncSession, store_id: str) -> list[Mailbox]:
    """The mailboxes linked to a store, with their send permission."""
    result = await db.execute(
        select(EmailAccount.email, EmailAccount.allow_send)
        .join(
            StoreEmailLink,
            StoreEmailLink.email_account_id == EmailAccount.id,
        )
        .where(StoreEmailLink.store_id == store_id)
    )
    return [Mailbox(email, bool(allow)) for email, allow in result.all()]


def store_email_section(
    store_id: str, mailboxes: Sequence[Mailbox] | None
) -> str:
    """The store prompt's ``## Email System`` section."""
    if not mailboxes:
        return f'\n\n## Email System\n{NO_STORE_MAILBOX}'
    n = len(mailboxes)
    emails_str = ', '.join(m.email for m in mailboxes)
    return (
        f'\n\n## Email System\n'
        f'This store has {n} connected email account(s): '
        f'{emails_str}.\n'
        f'Emails are synced to local SQLite DBs every '
        f'5 minutes automatically.\n\n'
        f'**To get DB paths and schema**, call:\n'
        f'  vibe_seller_email_info('
        f'store_id="{store_id}")\n'
        f'Then query directly via sqlite3 CLI, e.g.:\n'
        f'  sqlite3 <db_path> "SELECT subject, sender, '
        f"date FROM emails WHERE folder='INBOX' "
        f'ORDER BY date DESC LIMIT 20"\n\n'
        f'Available columns: message_id, folder, '
        f'subject, sender, recipient, date, body_text, '
        f'body_html, raw_headers, attachments, flags, '
        f'fetched_at, email_account\n\n'
        f'**To sync emails immediately** (instead of '
        f'waiting for the 5-min auto-sync), call:\n'
        f'  vibe_seller_sync_email_now('
        f'account_email="<email>")\n'
        f'Then query the DB as usual.\n\n'
        f'**To send email**, call:\n'
        f'  vibe_seller_send_email('
        f'account_email="...", to="...", '
        f'subject="...", body="...")\n'
        f'{send_policy_note(mailboxes)}\n\n'
        f'Prefer reading and analyzing email content '
        f'yourself using your language understanding '
        f'over writing scripts or regex to '
        f'classify/filter emails.'
    )
