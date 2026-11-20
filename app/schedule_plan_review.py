"""Plan review for review schedules — the single owner of its writes.

The state machine lives in ``app/plan_states.py``. This module owns the
three moments a plan changes hands:

- :func:`record_plan_proposal` — the planner (a plan-only Task) called
  ExitPlanMode: its plan becomes the schedule's proposal and the task
  ends. Called by the real ExitPlanMode hook and by the test FakeAgent,
  so both run the exact same transaction.
- :func:`approve_proposal` — the owner approved: the proposal becomes
  the plan every fire runs.
- :func:`check_plan_version` — the optimistic lock an approval or a
  change request carries, so nobody approves a plan they did not read.
"""

from datetime import UTC, datetime
import logging

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.events.bus import event_bus
from app.models.schedule import Schedule
from app.models.task import Task
from app.plan_states import PlanStatus
from app.task_states import TaskStatus
from app.telemetry_actions import record_plan_status_change

logger = logging.getLogger(__name__)

# Planner states from which ExitPlanMode may end the task. Anything
# else means another path already moved the task on (stop, retry, a
# racing executor) and the proposal must not land.
_PROPOSABLE = (TaskStatus.PLANNED, TaskStatus.DESIGNING, TaskStatus.QUEUED)


async def end_planner_with_proposal(
    db: AsyncSession, task: Task, schedule_id: str, plan_text: str
) -> bool:
    """End *task* and, if it is still current, propose its plan (no commit).

    The compare-and-swap on ``current_planning_task_id`` means an edit
    that started a newer planner in between always wins; the
    superseded task ends FAILED (``superseded_by_edit``). The approved
    ``plan`` is never touched. Returns True when the proposal landed.
    """
    now = datetime.now(UTC).isoformat()
    swapped = await db.execute(
        update(Schedule)
        .where(
            Schedule.id == schedule_id,
            Schedule.current_planning_task_id == task.id,
        )
        .values(
            proposed_plan=plan_text,
            plan_status=PlanStatus.PENDING_APPROVAL.value,
            plan_version=Schedule.plan_version + 1,
            plan_error=None,
            plan_error_code=None,
            current_planning_task_id=None,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    current = swapped.rowcount == 1
    if current:
        record_plan_status_change(
            db,
            schedule_id,
            PlanStatus.PLANNING.value,
            PlanStatus.PENDING_APPROVAL.value,
        )
        task.status = TaskStatus.COMPLETED
        task.completed_at = now
    else:
        task.status = TaskStatus.FAILED
        task.error = 'A newer planning run replaced this one.'
        task.error_category = 'superseded_by_edit'
    task.updated_at = now
    return current


async def emit_proposal_events(
    db: AsyncSession, task: Task, schedule_id: str, proposed: bool
) -> None:
    """SSE after the commit: the task's end, and the new proposal."""
    await event_bus.emit(
        'task_update', {'task_id': task.id, 'status': task.status}
    )
    if proposed:
        sched = await db.get(Schedule, schedule_id)
        if sched is not None:
            await db.refresh(sched)
        await event_bus.emit(
            'schedule_plan_pending_approval',
            {
                'schedule_id': schedule_id,
                'plan_version': sched.plan_version if sched else None,
            },
        )


async def record_plan_proposal(
    task_id: str, schedule_id: str, plan_text: str
) -> bool:
    """The planner called ExitPlanMode: end it and propose its plan.

    One transaction (see :func:`end_planner_with_proposal`). Only a
    task still in a planning state counts — anything else means
    another path (stop, retry) already moved it on. Returns True when
    the proposal landed.
    """
    async with async_session() as db:
        task = await db.get(Task, task_id)
        if not task:
            logger.error('Plan proposal: task %s missing', task_id)
            return False
        if task.status not in _PROPOSABLE:
            return False
        proposed = await end_planner_with_proposal(
            db, task, schedule_id, plan_text
        )
        await db.commit()
        await emit_proposal_events(db, task, schedule_id, proposed)
    return proposed


def check_plan_version(sched: Schedule, client_version: int | None) -> None:
    """412 if the caller read an older plan than the one now pending."""
    if client_version is not None and client_version != sched.plan_version:
        raise _version_conflict(client_version, sched.plan_version)


def _version_conflict(client: int, server: int) -> HTTPException:
    return HTTPException(
        status_code=412,
        detail=(
            f'The plan changed since you read it (you read version '
            f'{client}, it is now {server}). Read the plan again before '
            'deciding.'
        ),
    )


async def approve_proposal(
    db: AsyncSession, sched: Schedule, client_version: int | None
) -> None:
    """Promote the pending proposal to the approved plan, atomically.

    A compare-and-swap on ``plan_status='pending_approval'`` and the
    version the reviewer read: an edit that started a new planner, or
    a newer proposal, between the read and this write makes it fail
    (409 / 412) instead of approving a plan for an older prompt.
    """
    if sched.plan_status != PlanStatus.PENDING_APPROVAL.value:
        raise HTTPException(
            status_code=409,
            detail=(
                'No plan is waiting for approval '
                f'(plan_status={sched.plan_status!r}).'
            ),
        )
    check_plan_version(sched, client_version)
    expected = sched.plan_version
    now = datetime.now(UTC).isoformat()
    swapped = await db.execute(
        update(Schedule)
        .where(
            Schedule.id == sched.id,
            Schedule.plan_status == PlanStatus.PENDING_APPROVAL.value,
            Schedule.plan_version == expected,
        )
        .values(
            plan=Schedule.proposed_plan,
            proposed_plan=None,
            plan_feedback=None,
            plan_status=PlanStatus.READY.value,
            plan_version=Schedule.plan_version + 1,
            plan_error=None,
            plan_error_code=None,
            current_planning_task_id=None,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    if swapped.rowcount != 1:
        await db.rollback()
        await db.refresh(sched)
        raise _version_conflict(expected, sched.plan_version)
    record_plan_status_change(
        db,
        sched.id,
        PlanStatus.PENDING_APPROVAL.value,
        PlanStatus.READY.value,
    )
    await db.commit()
    await db.refresh(sched)
