"""What a task may do with each mailbox: the one place that decides.

Every surface asks here and nowhere else:
- the in-task email tools (``app/routers/task_mailboxes.py``);
- the send gate in ``app.email.sender``, which opens SMTP only when
  :func:`send_problem` is ``None``;
- the task prompt's one line about email (:func:`store_prompt_section`,
  :func:`all_mailboxes_line`).

So the prompt, the tool and the server can never disagree about whether a
mailbox can send.

A mailbox can or cannot read and send, and when it cannot the answer says
why and what the user changes, where. A task never has to guess, and
"not found" never sends it hunting the machine for another way. That was
observed: nine minutes of port scans, process environments and other
stores' cookie files after a task's real answer was done.

Scope: a store task uses only the mailboxes linked to its store. A task
with no store (an orchestrator) sees every mailbox.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email_account import EmailAccount
from app.models.store import Store
from app.models.store_email_link import StoreEmailLink

SETTINGS = 'Settings → Email'
SEND_PERMISSION_LOCATION = (
    'Settings → Email, "Email sending permission" switch on that mailbox'
)
_SEND_IT_YOURSELF = (
    'If the request needs an email sent, give the user the address and a '
    'draft to send themselves. For tasks to send mail, the user connects '
    f'a mailbox in {SETTINGS}.'
)
NO_STORE_MAILBOX = (
    'No mailbox is connected to this store, so this task cannot read or '
    'send its email. ' + _SEND_IT_YOURSELF
)
NO_MAILBOX = (
    'No mailbox is connected in Vibe Seller, so no task can send email. '
    + _SEND_IT_YOURSELF
)


def send_problem(account: EmailAccount) -> str | None:
    """Why *account* cannot send, or ``None`` when it can.

    Only the user's explicit off refuses: ``None`` on ``allow_send`` is a
    row not flushed yet, whose column default is on.
    """
    if account.allow_send is not None and not account.allow_send:
        return (
            'the user turned sending off for it; they turn it back on in '
            f'{SEND_PERMISSION_LOCATION}'
        )
    if not account.smtp_host:
        return (
            'it has no outgoing (SMTP) server set up; the user adds one '
            f'for it in {SETTINGS}'
        )
    return None


def read_problem(account: EmailAccount) -> str | None:
    """Why *account*'s mail cannot be read as current, or ``None``.

    Set while its last sync failed (``EmailAccount.sync_error``): the
    local copy still holds what arrived before, which is never shown as
    the inbox now.
    """
    if not account.sync_error:
        return None
    since = (
        f'the local copy stops at {account.synced_at}'
        if account.synced_at
        else 'it has never synced'
    )
    return (
        f'its inbox is not syncing ({account.sync_error}), so {since}; the '
        f'user fixes its password or server in {SETTINGS}'
    )


def send_refusal(account: EmailAccount, problem: str) -> str:
    """The message a refused send returns, written for the agent."""
    return (
        f'Nothing was sent from {account.email}: {problem}. Do not retry. '
        'Tell the user, and draft the email to show them instead.'
    )


@dataclass(frozen=True)
class MailboxAccess:
    """One mailbox as a task may use it."""

    account_id: str
    email: str
    stores: tuple[str, ...]
    read_problem: str | None
    send_problem: str | None

    @property
    def can_read(self) -> bool:
        return self.read_problem is None

    @property
    def can_send(self) -> bool:
        return self.send_problem is None

    def summary(self) -> str:
        """``a@x (read and send)``, or what it cannot do and why."""
        if self.can_read and self.can_send:
            return f'{self.email} (read and send)'
        if self.can_read:
            return f'{self.email} (read only: {self.send_problem})'
        if self.can_send:
            return f'{self.email} (send only: {self.read_problem})'
        return (
            f'{self.email} (cannot read: {self.read_problem}; cannot send: '
            f'{self.send_problem})'
        )

    def as_dict(self) -> dict:
        return {
            'account_id': self.account_id,
            'email': self.email,
            'can_read': self.can_read,
            'read_problem': self.read_problem,
            'can_send': self.can_send,
            'send_problem': self.send_problem,
        }


async def mailboxes_for(
    db: AsyncSession, store_id: str | None
) -> list[MailboxAccess]:
    """The mailboxes a task on *store_id* may use (every one when None)."""
    accounts = select(EmailAccount).order_by(EmailAccount.email)
    if store_id is not None:
        accounts = accounts.join(
            StoreEmailLink, StoreEmailLink.email_account_id == EmailAccount.id
        ).where(StoreEmailLink.store_id == store_id)
    rows = (await db.execute(accounts)).scalars().all()
    links = await db.execute(
        select(StoreEmailLink.email_account_id, Store.name).join(
            Store, Store.id == StoreEmailLink.store_id
        )
    )
    stores: dict[str, list[str]] = {}
    for account_id, name in links.all():
        stores.setdefault(account_id, []).append(name)
    return [
        MailboxAccess(
            account_id=a.id,
            email=a.email,
            stores=tuple(sorted(stores.get(a.id, []))),
            read_problem=read_problem(a),
            send_problem=send_problem(a),
        )
        for a in rows
    ]


def store_prompt_section(mailboxes: list[MailboxAccess]) -> str:
    """A store task prompt's ``## Email System`` section: one line.

    The heading and the addresses stay: skills compare a login's email
    with the bound addresses listed here (``noon-shared`` § 1).
    """
    if not mailboxes:
        return f'\n\n## Email System\n{NO_STORE_MAILBOX}'
    listed = '; '.join(m.summary() for m in mailboxes)
    return (
        f"\n\n## Email System\nThis store's mailboxes: {listed}. "
        "`vibe_seller_email_info` gives each one's database path and how to "
        'query it.'
    )


def all_mailboxes_line(mailboxes: list[MailboxAccess]) -> str | None:
    """The one email line of a no-store task's context, or None."""
    if not mailboxes:
        return None
    listed = '; '.join(
        f'{m.summary()} → {", ".join(m.stores) or "no store"}'
        for m in mailboxes
    )
    return (
        f'Email: {listed}. `vibe_seller_email_info` gives database paths '
        'and how to query them.'
    )
