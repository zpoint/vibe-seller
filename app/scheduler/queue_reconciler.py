"""Periodic reconciler for orphaned waiting-to-start tasks.

A task row that says it is waiting to start — QUEUED, or a scheduled
task still PENDING/PLANNED — is a promise that something in this
process will start it: a queue lane, a launcher coroutine, or a run
waiting on the agent-slot limiter (``app.task_owners`` lists them).
Any path that writes the row but never hands the task to one of those
holders leaves an orphan, and nothing ever starts it: freed agent
slots go to newer tasks, because the orphan is not waiting for one.
Only a restart recovered it — ``TaskQueueScheduler._recover_from_db``
re-queues every such row at boot.

Observed: a daily scheduled task sat ``queued`` for hours while
newer tasks took every slot that freed up, and started the instant
the server was restarted. One entry path is proven: waking a WAITING
task with no store (``POST /tasks/{id}/wake``, a message to a waiting
task, the waiting checker) wrote QUEUED but only submitted store tasks
to the queue. Fixing each entry path one at a time is how this class
keeps coming back, so this job closes it at the other end: every
minute it does for each orphan what a restart does for all of them,
and nothing else.

Scope, deliberately narrow:

- **QUEUED** — always a promise to run.
- **PENDING / PLANNED with a schedule** — a scheduled fire never parks
  a task for a person. An unscheduled PENDING task may be parked on
  purpose (``defer_start``: the client uploads attachments, then calls
  ``/start``), and an unscheduled PLANNED task waits for the user to
  press Run; neither is touched. A PENDING plan-only planner IS
  adopted (nothing parks a planner before it starts; the plan reaper
  leaves PENDING planners to this job), but a PLANNED one never is: it
  holds a written plan that only the plan reaper may turn into a
  proposal — re-queued, it would be EXECUTED.
- A row changed in the last ``_GRACE`` is skipped: every launch path
  writes the row first and hands the task over a moment later.
- A task with a live agent session is skipped — it is running.
"""

from datetime import UTC, datetime, timedelta
import logging

from sqlalchemy import and_, or_, select

from app import task_owners
from app.ai.claude_backend_manager import agent_manager
from app.database import async_session
from app.models.task import Task
from app.scheduler.task_queue import task_queue_scheduler
from app.task_states import TaskStatus
from app.telemetry_via import unattributed

logger = logging.getLogger(__name__)

# How long a row must have been unchanged before it can be an orphan.
# Launch paths commit the status and then submit within the same
# request, so a minute is far past any legitimate hand-over.
_GRACE = timedelta(seconds=60)


def _has_live_session(task_id: str) -> bool:
    session = agent_manager.get_session(task_id)
    return bool(session is not None and session.running)


async def reconcile_orphaned_tasks() -> int:
    """Re-queue waiting-to-start tasks that nothing will start.

    Returns how many tasks were re-queued.
    """
    if not task_queue_scheduler.is_running:
        return 0
    cutoff_iso = (datetime.now(UTC) - _GRACE).isoformat()
    async with async_session() as db:
        rows = (
            await db.execute(
                select(Task.id, Task.store_id, Task.status)
                .where(
                    or_(
                        Task.status == TaskStatus.QUEUED,
                        and_(
                            Task.status.in_([
                                TaskStatus.PENDING,
                                TaskStatus.PLANNED,
                            ]),
                            Task.schedule_id.is_not(None),
                            Task.is_plan_only.is_not(True),
                        ),
                        and_(
                            Task.status == TaskStatus.PENDING,
                            Task.is_plan_only.is_(True),
                        ),
                    ),
                    Task.updated_at < cutoff_iso,
                )
                .order_by(Task.created_at)
            )
        ).all()

    adopted = 0
    for task_id, store_id, status in rows:
        if task_owners.is_held(task_id) or _has_live_session(task_id):
            continue
        if await task_queue_scheduler.adopt_orphan(task_id, store_id):
            adopted += 1
            logger.warning(
                'Orphaned %s task %s had nothing to start it — re-queued '
                '(store=%s)',
                status,
                task_id,
                store_id,
            )
    return adopted


def register_queue_reconciler(scheduler) -> None:
    """Run the reconciler every minute, like the stall reaper.

    An orphan is back in its lane within ~2 min (``_GRACE`` + one
    interval) instead of waiting for a server restart.
    """
    scheduler.add_job(
        unattributed(reconcile_orphaned_tasks),
        'interval',
        minutes=1,
        id='queue_reconciler',
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info('Queue-reconciler job registered (every 1 min)')
