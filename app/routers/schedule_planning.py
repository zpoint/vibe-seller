"""Plan-at-creation lifecycle helpers for Schedule endpoints.

Extracted from ``app/routers/schedules.py`` so that router stays
under the 800-line repo limit. These helpers implement the
``PlanStatus`` transitions described in ``app/plan_states.py`` and
in ``docs/subsystems.md#plan-at-creation-lifecycle``:

- :func:`spawn_planning_task` — launches a plan-only Task that
  drives the agent through the plan-review flow.
- :func:`abort_current_planning_task` + :func:`finalize_pending_abort`
  — two-phase abort split to avoid a StaticPool rollback race between
  the caller's DB session and the agent session's concurrent writes.
- :func:`normalize_prompt` — collapse whitespace for change detection.
- :func:`schedule_needs_plan` — "is this a plan-mode user schedule?"
"""

from datetime import UTC, datetime
import logging
import re

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.claude_backend_manager import agent_manager
from app.database import async_session
from app.models.schedule import Schedule
from app.models.schedule_constants import PhaseMode
from app.models.task import Task
from app.models.user import User
from app.plan_states import PlanStatus
from app.routers.tasks import schedule_or_run
from app.task_states import TaskStatus, can_transition

logger = logging.getLogger(__name__)


_WHITESPACE_RE = re.compile(r'\s+')


def validate_finalize_description(
    finalize_description: str | None,
    store_id: str | None,
    phase_mode: str,
) -> None:
    """Reject finalize_description on a non-fanout schedule.

    It only drives an all-stores fanout batch (a reduce task after the
    per-store children finish); on a store-bound or single-phase
    schedule there is no batch, so it would silently never fire. Shared
    by schedule create + update. See app/scheduler/finalize_reaper.py.
    """
    if finalize_description and (
        store_id is not None or phase_mode != PhaseMode.FANOUT
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                'finalize_description is only valid for all-stores '
                'fanout schedules (store_id null + phase_mode=fanout).'
            ),
        )


def normalize_prompt(text: str | None) -> str:
    """Normalize a prompt for change-detection.

    Strip leading/trailing whitespace and collapse internal runs so a
    user re-saving the same prompt with cosmetic changes doesn't
    spuriously invalidate the plan.
    """
    if not text:
        return ''
    return _WHITESPACE_RE.sub(' ', text).strip()


def schedule_needs_plan(sched: Schedule) -> bool:
    """True iff this schedule goes through the plan-at-creation flow."""
    return bool(sched.plan_mode) and not sched.is_system


async def spawn_planning_task(
    schedule: Schedule,
    current_user: User,
    db: AsyncSession,
) -> Task:
    """Create a plan-only Task that authors the plan for a schedule.

    The Task runs through the normal auto-run pipeline in DESIGNING.
    The gate in ``task_runner_auto.py`` auto-approves any task with
    ``schedule_id`` set (``auto_approve_plan = bool(task.schedule_id) or
    not task.plan_mode``), so the hook commits the plan to
    ``Schedule.plan`` and terminates the Task at COMPLETED as soon as
    the agent calls ExitPlanMode — no manual approve click.

    Review + iteration happens afterwards via ``SchedulePlanPanel`` on
    the detail view: user can read the frozen plan, hit "Re-plan" to
    author a new version, or edit the schedule prompt (which flips
    ``plan_status`` to ``stale`` and requires a re-plan before the
    next fire).
    """
    task = Task(
        store_id=None,
        schedule_id=schedule.id,
        created_by=current_user.id,
        title=f'Plan: {schedule.title}',
        description=schedule.description or schedule.title,
        status=TaskStatus.PENDING,
        plan_mode=True,
        is_plan_only=True,
        ai_profile_id=schedule.ai_profile_id or 'default',
    )
    db.add(task)
    # Flush so we get the task.id before linking the Schedule pointer,
    # then commit Schedule + Task together below.
    await db.flush()
    schedule.current_planning_task_id = task.id
    schedule.plan_status = PlanStatus.PLANNING.value
    schedule.plan_error = None
    schedule.updated_at = datetime.now(UTC).isoformat()
    await db.commit()
    await db.refresh(task)

    # Launch — planning tasks are store-agnostic so they go through
    # the no-store direct-launch path.
    await schedule_or_run(task.id, None)
    return task


async def abort_current_planning_task(
    schedule: Schedule,
    db: AsyncSession,
    reason: str,
) -> None:
    """Stop + fail any in-flight plan-only task for a schedule.

    Called when the prompt is edited (plan is now stale) or the user
    requests a fresh /replan. Safe to call when no planning task is
    active.

    **Session isolation.** The agent-stop call cancels the FakeAgent /
    real-agent session, which may have a concurrent DB write in flight
    (e.g. ``_save_design_plan``). With SQLite StaticPool in tests, the
    shared connection means that session's rollback can clobber the
    caller's pending writes.  To avoid that, we only clear the pointer
    here (in the caller's session) and fail the Task row in a
    DEDICATED session after the caller has committed — see
    :func:`finalize_pending_abort`.
    """
    if not schedule.current_planning_task_id:
        return
    planning_task_id = schedule.current_planning_task_id
    # Clear the pointer in the caller's session so the caller's
    # commit persists it. Actual Task status transition and agent
    # stop happen after the caller commits (see the helper below).
    schedule.current_planning_task_id = None
    # Attach the pending work to the schedule object so the caller
    # knows to run it after commit. Using a private attribute keeps
    # the public signature stable.
    schedule._pending_abort_task_id = planning_task_id  # type: ignore[attr-defined]
    schedule._pending_abort_reason = reason  # type: ignore[attr-defined]


async def finalize_pending_abort(schedule: Schedule) -> None:
    """Finish the abort started by :func:`abort_current_planning_task`.

    Stops the agent session and transitions the planning Task to
    FAILED in a fresh DB session — safe to call after the caller's
    own commit has landed.
    """
    planning_task_id = getattr(schedule, '_pending_abort_task_id', None)
    if not planning_task_id:
        return
    reason = getattr(
        schedule,
        '_pending_abort_reason',
        'Planning task superseded',
    )
    # Agent stop first so the agent cooperative-cancels before we
    # overwrite its DB row.
    try:
        await agent_manager.stop(planning_task_id)
    except Exception:
        logger.debug(
            'Planning-task stop raised during abort',
            exc_info=True,
        )
    try:
        async with async_session() as fresh_db:
            task = await fresh_db.get(Task, planning_task_id)
            if task and can_transition(task.status, TaskStatus.FAILED):
                task.status = TaskStatus.FAILED
                task.error = reason
                task.error_category = 'superseded_by_edit'
                task.updated_at = datetime.now(UTC).isoformat()
                await fresh_db.commit()
    except Exception:
        logger.exception(
            'Failed to transition planning task %s to FAILED',
            planning_task_id,
        )
    # Clear the pending markers so repeat calls are no-ops.
    try:
        del schedule._pending_abort_task_id  # type: ignore[attr-defined]
    except AttributeError:
        pass
    try:
        del schedule._pending_abort_reason  # type: ignore[attr-defined]
    except AttributeError:
        pass
