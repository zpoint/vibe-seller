"""Periodic reaper for stuck plan-only tasks.

A plan-only Task can get stuck in DESIGNING/PLANNED/PENDING if the
agent process dies silently or the backend restarts mid-planning.
WAITING is legitimate (agent asked a user question) and is NOT
reaped — it has its own 30-day timeout in ``waiting.py``.
"""

from datetime import UTC, datetime, timedelta
import logging

from sqlalchemy import select

from app.ai.claude_backend_manager import agent_manager
from app.database import async_session
from app.events.bus import event_bus
from app.models.schedule import Schedule
from app.models.task import Task
from app.plan_states import PlanStatus
from app.task_states import TaskStatus, can_transition

logger = logging.getLogger(__name__)

# Reap a plan-only task whose row hasn't been touched in this long.
# Matches the semantics of "agent hasn't made progress" rather than
# "agent is waiting on the user" (WAITING is excluded below).
_STUCK_THRESHOLD_MINUTES = 30


async def reap_stuck_planning_tasks() -> None:
    """Fail Schedules stuck in PLANNING whose planner is idle.

    Scans all schedules with ``plan_status='planning'``. For each,
    loads the ``current_planning_task_id`` Task. If the Task is not
    in WAITING (legitimate user-input wait) and its ``updated_at`` is
    older than the threshold, transitions Task → FAILED, clears the
    pointer on the Schedule, and sets ``plan_status='failed'`` with
    a ``plan_error`` explaining the timeout.
    """
    cutoff = datetime.now(UTC) - timedelta(minutes=_STUCK_THRESHOLD_MINUTES)
    cutoff_iso = cutoff.isoformat()

    timed_out: list[tuple[str, str]] = []

    async with async_session() as db:
        result = await db.execute(
            select(Schedule).where(
                Schedule.plan_status == PlanStatus.PLANNING.value
            )
        )
        schedules = result.scalars().all()

        for sched in schedules:
            if not sched.current_planning_task_id:
                # Pointer dangling — repair.
                sched.plan_status = PlanStatus.FAILED.value
                sched.plan_error = (
                    'Planning task pointer missing; reset to FAILED.'
                )
                sched.updated_at = datetime.now(UTC).isoformat()
                continue

            task = await db.get(Task, sched.current_planning_task_id)
            if not task:
                sched.plan_status = PlanStatus.FAILED.value
                sched.plan_error = 'Planning task row deleted.'
                sched.current_planning_task_id = None
                sched.updated_at = datetime.now(UTC).isoformat()
                continue

            # WAITING is legitimate — user was asked a question;
            # they may take their time. Do not reap.
            if task.status == TaskStatus.WAITING:
                continue

            # Already terminal — schedule should have been flipped by
            # the hook, but sync up defensively.
            if task.status == TaskStatus.COMPLETED and sched.plan:
                sched.plan_status = PlanStatus.READY.value
                sched.current_planning_task_id = None
                sched.plan_error = None
                sched.updated_at = datetime.now(UTC).isoformat()
                continue
            # COMPLETED but the schedule has no plan = agent skipped
            # ExitPlanMode (model decided nothing to plan / chat-only
            # reply / etc). The finalizer's is_plan_only guard now
            # catches this before Task ends at COMPLETED, but older
            # schedules stuck pre-fix still land here — flip the
            # schedule so the UI / reaper surface the error instead
            # of silently staying in `planning` forever.
            if task.status == TaskStatus.COMPLETED and not sched.plan:
                sched.plan_status = PlanStatus.FAILED.value
                sched.plan_error = (
                    task.error
                    or 'Planning task completed without calling '
                    'ExitPlanMode — no plan was saved.'
                )
                sched.current_planning_task_id = None
                sched.updated_at = datetime.now(UTC).isoformat()
                continue
            if task.status == TaskStatus.FAILED:
                sched.plan_status = PlanStatus.FAILED.value
                sched.plan_error = (
                    task.error or 'Planning task failed without a message.'
                )
                sched.current_planning_task_id = None
                sched.updated_at = datetime.now(UTC).isoformat()
                continue

            # Stuck check.
            if (task.updated_at or '') > cutoff_iso:
                continue

            logger.warning(
                'Reaping stuck planning task %s (schedule=%s, status=%s)',
                task.id,
                sched.id,
                task.status,
            )
            try:
                await agent_manager.stop(task.id)
            except Exception:
                logger.debug(
                    'stop() raised for stuck planning task %s',
                    task.id,
                    exc_info=True,
                )

            if can_transition(task.status, TaskStatus.FAILED):
                task.status = TaskStatus.FAILED
                task.error = 'Planning timed out'
                task.error_category = 'planning_timeout'
                task.updated_at = datetime.now(UTC).isoformat()

            sched.plan_status = PlanStatus.FAILED.value
            sched.plan_error = 'Planning timed out'
            sched.current_planning_task_id = None
            sched.updated_at = datetime.now(UTC).isoformat()

            timed_out.append((sched.id, task.id))

        await db.commit()

    # Emit only after the commit succeeds — avoids consumers
    # observing a timeout event for a schedule still marked
    # 'planning' in the DB.
    for schedule_id, task_id in timed_out:
        await event_bus.emit(
            'schedule_plan_timeout',
            {'schedule_id': schedule_id, 'task_id': task_id},
        )
