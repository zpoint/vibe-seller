"""Email: per-account SQLite mailboxes, sync, and sending.

What an agent is told when there is no mailbox to use. A task can only
read or send mail through a mailbox connected in Settings → Email; with
none, saying so plainly is what keeps an agent from hunting for another
way to send (observed: nine minutes of port scans, process environments
and other stores' cookie files after a task's real answer was done).
"""

_SEND_IT_YOURSELF = (
    'If the request needs an email sent, give the user the address and a '
    'draft to send themselves. For tasks to send mail, the user connects '
    'a mailbox in Settings → Email.'
)

NO_STORE_MAILBOX = (
    'No mailbox is connected to this store, so this task cannot read or '
    'send its email. ' + _SEND_IT_YOURSELF
)

NO_MAILBOX = (
    'No mailbox is connected in Vibe Seller, so no task can send email. '
    + _SEND_IT_YOURSELF
)
