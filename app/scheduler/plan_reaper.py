"""Periodic reconciler for review-schedule planners.

Scans schedules in ``plan_status='planning'`` and decides about their
planner (the plan-only Task ``current_planning_task_id`` points at)
by whether anything can still make it progress — never by how long it
has been waiting:

- **Waiting is not stuck.** PENDING / QUEUED (waiting for an agent
  slot or its turn — orphans are re-queued by ``queue_reconciler``,
  the way a restart would) and WAITING (a question to the user) are
  never touched. A proposal awaiting the owner's approval is not even
  in scope: ``pending_approval`` is not ``planning``, and the wait for
  a human never expires.
- **A planner with a live launch is alive.** Held by its launcher
  (``task_owners``), between ``run()`` and process spawn
  (``agent_manager.is_launching``), or with a live agent process that
  has not outlasted ``stall_reaper``'s stream-silence bound
  (:func:`app.scheduler.stall_reaper.agent_cannot_progress`).
- **Dead → failed.** A DESIGNING/PLANNED/RUNNING planner nothing can
  advance any more (process gone, e.g. after a crash) is failed with
  ``error_category='planner_died'``; the owner re-plans.
- **A written plan is never lost.** A PLANNED planner with a saved plan
  and no live agent (the server died between saving the plan and
  handing it over) becomes the schedule's proposal, as the hook would
  have made it.
- Terminal planners are reconciled: COMPLETED with a plan → proposal,
  otherwise ``failed`` with the reason.
"""

import asyncio
from datetime import UTC, datetime
import logging

from sqlalchemy import select

from app import task_owners
from app.ai.claude_backend_manager import agent_manager
from app.database import async_session
from app.events.bus import event_bus
from app.models.schedule import Schedule
from app.models.task import Task
from app.plan_states import PlanFailure, PlanStatus, fail_plan
from app.schedule_plan_review import (
    emit_proposal_events,
    end_planner_with_proposal,
)
from app.scheduler.stall_reaper import agent_cannot_progress
from app.task_reaper import reap_task_processes
from app.task_states import TaskStatus, can_transition

logger = logging.getLogger(__name__)

# The planner is waiting its turn or for an answer: never reaped.
_WAITING = (TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.WAITING)

PLANNER_DIED_MESSAGE = (
    'The planning agent stopped before it finished the plan (for '
    'example, the server restarted). Start the planning again.'
)


# A failed planner's error_category → why the plan failed, as the
# code the UI translates (PlanFailure). Unknown categories are OTHER.
_FAILURE_OF_CATEGORY = {
    'server_restart': PlanFailure.SERVER_RESTART,
    'stopped_by_user': PlanFailure.STOPPED,
    'planner_died': PlanFailure.PLANNER_DIED,
    'planning_launch_failed': PlanFailure.START_FAILED,
}


def _fail_schedule(
    sched: Schedule, code: PlanFailure, message: str | None
) -> None:
    fail_plan(sched, code, message)
    sched.current_planning_task_id = None
    sched.updated_at = datetime.now(UTC).isoformat()


def _planner_live(task: Task, now: datetime) -> bool:
    """Whether anything can still advance this planner."""
    if task_owners.is_held(task.id) and not agent_manager.is_process_alive(
        task.id
    ):
        # Its launcher is preparing the run or unwinding it; the
        # launcher writes the outcome.
        return True
    if agent_manager.is_launching(task.id):
        return True
    return not agent_cannot_progress(task, now)


async def reap_stuck_planning_tasks() -> None:
    """Reconcile every ``planning`` schedule with its planner's reality."""
    now = datetime.now(UTC)
    died: list[tuple[str, str]] = []
    proposals: list[tuple[Task, str]] = []

    async with async_session() as db:
        result = await db.execute(
            select(Schedule).where(
                Schedule.plan_status == PlanStatus.PLANNING.value
            )
        )
        for sched in result.scalars().all():
            task_id = sched.current_planning_task_id
            task = await db.get(Task, task_id) if task_id else None
            if task is None:
                # Creation sets pointer + status atomically, so
                # `planning` with no planner means nothing is coming.
                _fail_schedule(
                    sched, PlanFailure.PLANNER_DIED, PLANNER_DIED_MESSAGE
                )
                continue
            if task.status in _WAITING:
                continue
            if task.status == TaskStatus.COMPLETED:
                if task.plan:
                    proposals.append((task, sched.id))
                else:
                    _fail_schedule(
                        sched,
                        PlanFailure.NO_PLAN,
                        task.error
                        or 'Planning task completed without calling '
                        'ExitPlanMode — no plan was saved.',
                    )
                continue
            if task.status == TaskStatus.FAILED:
                _fail_schedule(
                    sched,
                    _FAILURE_OF_CATEGORY.get(
                        task.error_category or '', PlanFailure.OTHER
                    ),
                    task.error or 'The planning run failed.',
                )
                continue
            if _planner_live(task, now):
                continue
            if task.status == TaskStatus.PLANNED and task.plan:
                # Died between saving the plan and the hand-over.
                proposals.append((task, sched.id))
                continue

            logger.warning(
                'Planner %s for schedule %s can no longer progress '
                '(status=%s, no live agent) — failing it',
                task.id,
                sched.id,
                task.status,
            )
            try:
                await agent_manager.stop(task.id)
            except Exception:
                logger.debug(
                    'stop() raised for dead planner %s',
                    task.id,
                    exc_info=True,
                )
            if can_transition(task.status, TaskStatus.FAILED):
                task.status = TaskStatus.FAILED
                task.error = PLANNER_DIED_MESSAGE
                task.error_category = 'planner_died'
                task.updated_at = now.isoformat()
            _fail_schedule(
                sched, PlanFailure.PLANNER_DIED, PLANNER_DIED_MESSAGE
            )
            died.append((sched.id, task.id))

        landed = []
        for task, schedule_id in proposals:
            ended = task.status == TaskStatus.COMPLETED
            snapshot = (task.completed_at, task.error, task.error_category)
            proposed = await end_planner_with_proposal(
                db, task, schedule_id, task.plan
            )
            if ended and not proposed:
                # Already a finished run; only the hand-over lost.
                task.status = TaskStatus.COMPLETED
                task.completed_at, task.error, task.error_category = snapshot
            landed.append((task, schedule_id, proposed))

        await db.commit()

        for task, schedule_id, proposed in landed:
            await emit_proposal_events(db, task, schedule_id, proposed)

    # Emit only after the commit succeeds — avoids consumers
    # observing a failure for a schedule still marked 'planning'.
    for schedule_id, task_id in died:
        await event_bus.emit(
            'schedule_plan_failed',
            {'schedule_id': schedule_id, 'task_id': task_id},
        )
        # The planner's agent is gone, but its detached children may
        # not be — reap them (nohup / ssh -f escape the process group).
        asyncio.create_task(reap_task_processes(task_id))
