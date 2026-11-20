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
from app.ai.profiles import resolve_schedule_profile
from app.config import AI_BOT_ROLE
from app.database import async_session
from app.models.schedule import Schedule
from app.models.schedule_constants import PhaseMode
from app.models.task import Task
from app.models.user import User
from app.plan_states import (
    PlanStatus,
    is_review_schedule,
    review_plan_fires,
)
from app.task_launch import schedule_or_run
from app.task_states import TaskStatus, can_transition
from app.telemetry_events import TaskSource
from app.telemetry_tasks import send_task_created

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


def validate_fanout_serial(
    fanout_serial: bool | None,
    store_id: str | None,
    phase_mode: str,
) -> None:
    """Reject fanout_serial on a schedule that does not fan out.

    A store-bound or single-phase schedule fires one task, so there are
    no siblings to run one at a time and the flag would silently do
    nothing. Same reasoning, same rule as
    :func:`validate_finalize_description`.
    """
    if fanout_serial and (
        store_id is not None or phase_mode != PhaseMode.FANOUT
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                'fanout_serial is only valid for all-stores fanout '
                'schedules (store_id null + phase_mode=fanout).'
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
    """True iff this is a review schedule (plan approved before firing).

    Auto schedules (``plan_mode=False``, the default) and system
    schedules fire their prompt as-is.
    """
    return is_review_schedule(sched)


def drop_review_state(sched: Schedule) -> None:
    """Forget every plan artefact: the schedule now fires its prompt.

    Used when review is switched off. The approved plan goes too — an
    auto schedule that kept one would look reviewed while firing the
    prompt. The caller aborts any in-flight planner first.
    """
    sched.plan = None
    sched.proposed_plan = None
    sched.plan_feedback = None
    sched.plan_error = None
    sched.plan_status = PlanStatus.NONE.value


async def spawn_planning_task(
    schedule: Schedule,
    current_user: User,
    db: AsyncSession,
) -> Task:
    """Create a plan-only Task that writes the plan for a review schedule.

    The Task runs through the normal auto-run pipeline in DESIGNING.
    When the agent calls ExitPlanMode, the hook records the plan as a
    PROPOSAL (``Schedule.proposed_plan``, ``plan_status=
    pending_approval``) and ends the Task at COMPLETED — the agent
    process exits and its slot is freed. Nothing is approved here: the
    owner approves or requests changes later via
    ``POST /api/schedules/{id}/plan/approve|request-changes`` (the
    banner + ``SchedulePlanPanel``, or the MCP ``review_schedule_plan``
    tool), and that wait costs nothing and never expires.

    The profile comes from :func:`resolve_schedule_profile`, the same
    resolver the fire path uses, so the plan is authored by the
    provider that will run the schedule. Reading
    ``schedule.ai_profile_id`` directly missed that: ``'default'`` is
    the inherit sentinel *and* the column default, so every unpinned
    schedule planned on the empty-env profile no matter what its
    owner had configured.
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
        source=TaskSource.SCHEDULE,
        ai_profile_id=await resolve_schedule_profile(schedule, db),
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
    send_task_created(task, user_id=current_user.id)

    # Launch — planning tasks are store-agnostic so they go through
    # the no-store direct-launch path.
    await schedule_or_run(task.id, None)
    return task


async def spawn_planning_task_or_fail(
    schedule: Schedule,
    current_user: User,
    db: AsyncSession,
) -> None:
    """Start the planner; on failure record it on the schedule.

    Never raises: a schedule that could not start its planner is
    ``failed`` with the reason, and the UI/MCP offer re-plan. Clears
    the orphaned pointer + task a partial spawn left, so /replan's
    idempotency check is not tricked into returning a never-started
    task.
    """
    try:
        await spawn_planning_task(schedule, current_user, db)
    except Exception as e:
        logger.exception(
            'Failed to spawn planning task for schedule %s', schedule.id
        )
        if schedule.current_planning_task_id:
            orphan = await db.get(Task, schedule.current_planning_task_id)
            if orphan and can_transition(orphan.status, TaskStatus.FAILED):
                orphan.status = TaskStatus.FAILED
                orphan.error = f'Planning task launch failed: {e}'
                orphan.error_category = 'planning_launch_failed'
                orphan.updated_at = datetime.now(UTC).isoformat()
        schedule.current_planning_task_id = None
        schedule.plan_status = PlanStatus.FAILED.value
        schedule.plan_error = f'Failed to spawn planning task: {e}'
        await db.commit()


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


async def retry_schedule_plan(
    task: Task, db: AsyncSession
) -> tuple[bool, str | None, int | None] | None:
    """How a retry of a schedule-owned task restarts — decided up front.

    Returns ``None`` to leave the task's mode alone, else
    ``(plan_mode, plan, plan_version)`` for the retried run. Raises 409
    when the retry would run something nobody approved. Called before
    the retry wipes anything, so a refusal changes nothing.

    - A planner (``is_plan_only``) re-authors: it becomes the
      schedule's current planner again (``plan_status=planning``) —
      unless the schedule went auto or a newer planner exists.
    - A fire (``plan_mode`` task) follows its schedule's CURRENT mode:
      an auto schedule retries as a plain auto run; a review schedule
      re-seeds its approved plan, and with no approved plan the retry
      is refused (it would otherwise plan for itself).
    - A non-plan-mode task keeps its recorded mode.
    """
    if not task.schedule_id or not (task.plan_mode or task.is_plan_only):
        return None
    sched = await db.get(Schedule, task.schedule_id)
    if sched is None:
        return None
    if task.is_plan_only:
        if not schedule_needs_plan(sched):
            raise HTTPException(
                status_code=409,
                detail=(
                    'This schedule no longer reviews a plan (it runs '
                    'automatically), so there is nothing to plan.'
                ),
            )
        if sched.current_planning_task_id not in (None, task.id):
            raise HTTPException(
                status_code=409,
                detail=(
                    'A newer planning run for this schedule is already '
                    'in progress.'
                ),
            )
        sched.current_planning_task_id = task.id
        sched.plan_status = PlanStatus.PLANNING.value
        sched.plan_error = None
        sched.updated_at = datetime.now(UTC).isoformat()
        return None
    if not schedule_needs_plan(sched):
        return (False, None, None)
    if review_plan_fires(sched):
        return (True, sched.plan, sched.plan_version)
    raise HTTPException(
        status_code=409,
        detail=(
            "This schedule's plan has not been approved yet, so this "
            'run cannot be retried. Approve the plan on the schedule '
            'first.'
        ),
    )


def refuse_task_agent_review_off(user: User, review_change: bool | None):
    """403 when an in-task agent tries to switch plan review OFF.

    Review off makes fires run the prompt with no approved plan — a
    task agent (every in-task credential resolves to the ``ai_bot``
    user) must not be able to walk around the review it is held to.
    Turning review ON, or editing the prompt (which re-plans for
    approval), stays open to it.
    """
    if review_change is False and user.role == AI_BOT_ROLE:
        raise HTTPException(
            status_code=403,
            detail=(
                'Only the schedule owner can switch plan review off. A '
                'task agent cannot.'
            ),
        )


def not_firing_detail(schedule: Schedule) -> str:
    """Why a review schedule cannot fire, and what unblocks it."""
    if schedule.plan_status == PlanStatus.PENDING_APPROVAL.value:
        return (
            'This schedule runs only an approved plan, and its plan is '
            'waiting for approval (plan_status=pending_approval). '
            'Approve it first.'
        )
    if schedule.plan_status == PlanStatus.PLANNING.value:
        return (
            'This schedule runs only an approved plan; its plan is being '
            'written now (plan_status=planning). Approve it once it is '
            'ready.'
        )
    return (
        'This schedule runs only an approved plan and has none '
        f'(plan_status={schedule.plan_status!r}). Re-plan, then approve.'
    )
