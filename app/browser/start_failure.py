"""A failed store-browser start, surfaced as a typed task failure.

The contract (BRIEF-2 PR C2 / incident 97db66d2): when a store
browser start fails with an :class:`InjectError`, the HTTP error body
the wrapper sees carries the stable ``code`` plus the message, and a
TERMINAL failure is recorded on the task (error + category), stops
the agent and ends the task FAILED — so the failure banner and its
「如何解决 ↗」 docs link render and the agent never treats a bare
``HTTP 500`` as a puzzle to solve.

This module owns the task layer of that contract. The browser layer
(the taxonomy) lives in :mod:`app.browser.inject.errors`; the wrapper
line the agent reads is generated in :mod:`app.browser.wrapper`.
"""

from datetime import UTC, datetime
import logging

from app.ai.claude_backend_manager import agent_manager
from app.browser.inject.errors import InjectError
from app.database import async_session
from app.events.bus import event_bus
from app.models.task import Task
from app.task_runner import check_parent_completion
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

    Mirrors ``POST /api/tasks/{id}/agent/stop``: the FAILED commit
    happens FIRST so the run's own finalizer
    (``_finalize_terminal_state``) sees a terminal status and bails
    instead of overwriting it; then the agent is stopped. Returns True
    when the task was failed here, False when nothing was done.

    The task id arrives in a request HEADER the wrapper sends — which
    an agent can send too, with any value, since the wrapper's
    credentials live in its env. So the header only SELECTS a task;
    it can never make the route fail one that is not already this
    store's own live run: the task must exist, be RUNNING/DESIGNING
    (anything else cannot transition to FAILED), and belong to THIS
    store. Anything else is ignored — the typed error still answers.
    """
    error = typed_error_text(exc)
    async with async_session() as db:
        task = await db.get(Task, task_id)
        if (
            task is None
            or task.store_id != store_id
            or not can_transition(task.status, TaskStatus.FAILED)
        ):
            return False
        task.status = TaskStatus.FAILED
        task.error = error
        task.error_category = category_for(exc)
        task.updated_at = datetime.now(UTC).isoformat()
        await db.commit()
    logger.warning(
        '%s: task %s (store %s) ended FAILED — [%s] %s. '
        'The agent was stopped: this error needs the operator, '
        'not the agent.',
        TERMINAL_STOP_LOG_MARKER,
        task_id,
        store_name,
        exc.code,
        error,
    )
    await event_bus.emit(
        'task_update',
        {
            'task_id': task_id,
            'status': TaskStatus.FAILED,
            'error': error,
            'error_category': category_for(exc),
        },
    )
    await agent_manager.stop(task_id)
    # A fanout child that dies here must not strand its WAITING
    # parent — same call the user-stop route makes.
    async with async_session() as db:
        task = await db.get(Task, task_id)
        await check_parent_completion(task, db)
    return True
