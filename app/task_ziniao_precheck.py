"""Bring Ziniao up before a task's agent starts.

Every task on a Ziniao store — auto mode, plan mode, execute-after-plan
and a woken task — runs :func:`ensure_ziniao_before_run` right before
``agent_manager.run``. Ziniao that is not answering (opened by hand in
normal mode, not running, leftovers from a crash) is killed and
relaunched in WebDriver mode; nobody is asked. Only a start that really
fails fails the task, and its ``error_category`` comes from the
exception, not from parsing its text.
"""

from __future__ import annotations

from datetime import UTC, datetime
import logging

from app.browser.manager import browser_manager
from app.database import async_session
from app.errors import ziniao_error_category
from app.events.bus import event_bus
from app.models.store import Store
from app.models.task import Task
from app.task_states import TaskStatus, assert_transition

logger = logging.getLogger(__name__)


async def ensure_ziniao_before_run(task_id: str, store: Store | None) -> bool:
    """True when the task may run; False when it was marked FAILED."""
    if not store or store.browser_backend != 'ziniao':
        return True
    try:
        async with async_session() as db:
            await browser_manager.ensure_ziniao_ready(store, db)
        return True
    except RuntimeError as exc:
        logger.warning('Ziniao not ready for task %s: %s', task_id, exc)
        error = str(exc)
        async with async_session() as db:
            task = await db.get(Task, task_id)
            if not task:
                return False
            assert_transition(task.status, TaskStatus.FAILED)
            task.status = TaskStatus.FAILED
            task.error = error
            task.error_category = ziniao_error_category(exc)
            task.updated_at = datetime.now(UTC).isoformat()
            await db.commit()
        await event_bus.emit(
            'task_update',
            {'task_id': task_id, 'status': TaskStatus.FAILED, 'error': error},
        )
        return False
