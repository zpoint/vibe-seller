"""A failed store-browser start, surfaced as a typed task failure.

The contract (BRIEF-2 PR C2 / incident 97db66d2): when a store
browser start fails with an :class:`InjectError`, the HTTP error body
the wrapper sees carries the stable ``code`` plus the message, and a
TERMINAL failure is recorded on the task (error + category), stops
the agent and ends the task FAILED — so the failure banner and its
「如何解决 ↗」 docs link render and the agent never treats a bare
``HTTP 500`` as a puzzle to solve.

This module owns the task layer of that contract: the typed body
shapes, and the guard that decides which task a terminal failure may
end. The browser layer (the taxonomy) lives in
:mod:`app.browser.inject.errors`; the wrapper line the agent reads is
generated in :mod:`app.browser.wrapper`. The terminal write itself is
NOT done here — both this path and the user's Stop go through the one
external-stop writer, :func:`app.task_finalize.stop_as_failed`.
"""

import logging

from app.browser.inject.errors import InjectError
from app.database import async_session
from app.models.task import Task
from app.task_finalize import stop_as_failed
from app.task_states import TaskStatus, can_transition

logger = logging.getLogger(__name__)

#: Stable marker in the server log for "a terminal browser-start
#: failure ended a task". The tier-1 e2e asserts on this line.
TERMINAL_STOP_LOG_MARKER = 'terminal browser-start failure'


def error_body(exc: InjectError) -> dict:
    """The HTTP error detail for a failed browser start.

    Carries the stable ``code`` (what the wrapper's typed line and the
    docs-link mapping key on), the message and the operator hint.
    """
    return {'code': exc.code, 'message': str(exc), 'hint': exc.hint}


def typed_error_text(exc: InjectError) -> str:
    """The task-error text: the message prefixed with its stable code.

    ``UnsupportedKernel`` already prefixes its own code (its ``__str__``
    is what the UI's docs mapping matches), so it is never re-prefixed.
    """
    text = str(exc)
    prefix = f'[{exc.code}]'
    return text if text.startswith(prefix) else f'{prefix} {text}'


def category_for(exc: InjectError) -> str:
    """The task's ``error_category``: the stable code, snake-cased.

    The frontend maps this (not prose) to the docs topic —
    ``TASK_ERROR_HELP`` in ``frontend/src/lib/docsHelp.ts``.
    """
    return exc.code.lower()


async def fail_task_for_start_error(
    task_id: str, exc: InjectError, store_id: str, store_name: str
) -> bool:
    """End a task FAILED for a terminal browser-start failure.

    The task id arrives in a request HEADER the wrapper sends — which
    an agent can send too, with any value, since the wrapper's
    credentials live in its env. So the header only SELECTS a task;
    it can never make the route fail one that is not already this
    store's own live agent run: the task must exist, be RUNNING or
    DESIGNING (a QUEUED, PLANNED or WAITING task is waiting for its
    turn or a person, not for a browser — and the state machine
    would allow FAILED from those, so this is an explicit check, not
    ``can_transition`` alone), and belong to THIS store. Anything
    else is ignored — the typed error still answers.

    The selected task is then ended through the ONE external-stop
    writer (:func:`app.task_finalize.stop_as_failed`) — the same one
    the user's Stop route uses, not a second copy of its block.
    """
    error = typed_error_text(exc)
    async with async_session() as db:
        task = await db.get(Task, task_id)
        if (
            task is None
            or task.store_id != store_id
            or task.status not in (TaskStatus.RUNNING, TaskStatus.DESIGNING)
            or not can_transition(task.status, TaskStatus.FAILED)
        ):
            return False
    stopped = await stop_as_failed(task_id, error, category_for(exc))
    if stopped:
        logger.warning(
            '%s: task %s (store %s) ended FAILED — %s. '
            'The agent was stopped: this error needs the operator, '
            'not the agent.',
            TERMINAL_STOP_LOG_MARKER,
            task_id,
            store_name,
            error,
        )
    return stopped
