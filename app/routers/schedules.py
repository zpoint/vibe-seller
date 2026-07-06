"""CRUD router for scheduled tasks (Schedule model).

The Schedule DB table is the source of truth. APScheduler jobs
(MemoryJobStore) are rebuilt on startup and kept in sync on
create / update / delete / pause / resume.
"""

import asyncio
from datetime import UTC, datetime
import logging
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.profiles import resolve_schedule_profile
from app.auth import get_current_user
from app.database import async_session, get_db
from app.events.bus import event_bus
from app.models.app_settings import AppSettings
from app.models.schedule import Schedule
from app.models.schedule_constants import (
    USER_SELECTABLE_PHASE_MODES,
    PhaseMode,
)
from app.models.store import Store
from app.models.task import Task
from app.models.user import User
from app.plan_states import PlanStatus
from app.routers.schedule_planning import (
    abort_current_planning_task,
    finalize_pending_abort,
    normalize_prompt,
    schedule_needs_plan,
    spawn_planning_task,
    validate_finalize_description,
)
from app.scheduler.cron import (
    add_schedule_job,
    get_schedule_next_run,
    pause_schedule_job,
    remove_schedule_job,
)
from app.scheduler.fanout import (
    _cancel_prior_waiting,
    run_fanout_job,
    run_single_job,
)
from app.scheduler.task_queue import task_queue_scheduler
from app.schemas.schedule import (
    ScheduleCreate,
    SchedulePlanResponse,
    SchedulePlanTaskSummary,
    ScheduleResponse,
    ScheduleUpdate,
)
from app.schemas.task import TaskResponse
from app.task_states import TaskStatus, can_transition
from app.utils.timezone import get_server_timezone

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/api/schedules', tags=['schedules'])


def _validate_timezone(tz: str) -> None:
    """Raise 400 if timezone string is invalid."""
    try:
        ZoneInfo(tz)
    except (KeyError, Exception):
        raise HTTPException(
            status_code=400,
            detail=f'Invalid timezone: {tz}',
        )


def _validate_schedule_type(schedule_type: str) -> None:
    """Raise 400 if schedule_type is not recognized."""
    valid = ('minutes', 'hours', 'days', 'weekly', 'monthly')
    if schedule_type not in valid:
        raise HTTPException(
            status_code=400,
            detail=(
                f'Invalid schedule_type: {schedule_type}. '
                f'Must be one of {valid}'
            ),
        )


def _validate_schedule_time(schedule_time: str) -> None:
    """Raise 400 if schedule_time is not HH:MM or HH:MM:SS."""
    parts = schedule_time.split(':')
    if len(parts) not in (2, 3):
        raise HTTPException(
            status_code=400,
            detail=('Invalid schedule_time format. Expected HH:MM or HH:MM:SS'),
        )
    try:
        h, m = int(parts[0]), int(parts[1])
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError
        if len(parts) == 3:
            s = int(parts[2])
            if not 0 <= s <= 59:
                raise ValueError
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail='Invalid schedule_time values',
        )


async def _resolve_phase_mode(
    db: AsyncSession,
    store_id: str | None,
    requested: str | None,
) -> str:
    """Pick the phase_mode for a new schedule.

    Precedence:
    - Store-bound schedule (``store_id`` set) → always ``'single'``.
    - Client passed a user-selectable mode → honor it.
    - Fall back to ``AppSettings.default_schedule_phase_mode``.
    - Fall back to ``PhaseMode.FANOUT``.

    Rejects ``'two_phase'`` from clients (system seeds use it directly).
    """
    if store_id is not None:
        return PhaseMode.SINGLE.value

    if requested is not None:
        if requested not in USER_SELECTABLE_PHASE_MODES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f'Invalid phase_mode: {requested}. '
                    f'Must be one of {sorted(USER_SELECTABLE_PHASE_MODES)}'
                ),
            )
        return requested

    row = await db.get(AppSettings, 'default_schedule_phase_mode')
    if row and row.value in USER_SELECTABLE_PHASE_MODES:
        return row.value
    return PhaseMode.FANOUT.value


async def _enrich_response(
    schedule: Schedule,
) -> ScheduleResponse:
    """Build a ScheduleResponse with computed fields."""
    resp = ScheduleResponse.model_validate(schedule)

    # next_run from APScheduler
    resp.next_run = get_schedule_next_run(schedule.id)

    # child_task_count + last_run_status + pending_questions_count from DB
    async with async_session() as db:
        # Count child tasks (plan-only tasks excluded — they are a
        # creation-time artefact, not a schedule run).
        count_result = await db.execute(
            select(func.count())
            .select_from(Task)
            .where(
                Task.schedule_id == schedule.id,
                Task.is_plan_only.is_(False),
            )
        )
        resp.child_task_count = count_result.scalar() or 0

        # Latest fire's status.
        latest = await db.execute(
            select(Task.status)
            .where(
                Task.schedule_id == schedule.id,
                Task.is_plan_only.is_(False),
            )
            .order_by(Task.created_at.desc())
            .limit(1)
        )
        row = latest.first()
        resp.last_run_status = row[0] if row else None

        # Count WAITING child tasks so the UI can badge schedules
        # that need user input at fire-time.
        waiting_count = await db.execute(
            select(func.count())
            .select_from(Task)
            .where(
                Task.schedule_id == schedule.id,
                Task.status == TaskStatus.WAITING,
            )
        )
        resp.pending_questions_count = waiting_count.scalar() or 0

    return resp


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.get('', response_model=list[ScheduleResponse])
async def list_schedules(
    store_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """List schedules, optionally filtered by store_id."""
    q = select(Schedule).order_by(Schedule.created_at.desc())
    if store_id:
        q = q.where(Schedule.store_id == store_id)
    result = await db.execute(q)
    schedules = result.scalars().all()
    return [await _enrich_response(s) for s in schedules]


@router.post('', response_model=ScheduleResponse, status_code=201)
async def create_schedule(
    data: ScheduleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new schedule and register its APScheduler job."""
    _validate_schedule_type(data.schedule_type)
    _validate_schedule_time(data.schedule_time)

    # Resolve timezone: explicit → AppSettings default → server local.
    if data.timezone is None:
        row = await db.get(AppSettings, 'default_schedule_timezone')
        data.timezone = (
            row.value if row and row.value else get_server_timezone()
        )
    _validate_timezone(data.timezone)

    # Resolve store (for platform/country)
    platform = None
    country = None
    if data.store_id:
        store = await db.get(Store, data.store_id)
        if not store:
            raise HTTPException(status_code=404, detail='Store not found')

    phase_mode = await _resolve_phase_mode(db, data.store_id, data.phase_mode)

    validate_finalize_description(
        data.finalize_description, data.store_id, phase_mode
    )

    # Force plan_mode=True for all user-created schedules — the
    # plan-at-creation lifecycle is the only UX (system seeds stay
    # plan_mode=False). See app/plan_states.py.
    effective_plan_mode = True

    schedule = Schedule(
        store_id=data.store_id,
        title=data.title,
        description=data.description,
        platform=platform,
        country=country,
        schedule_type=data.schedule_type,
        schedule_time=data.schedule_time,
        schedule_day=data.schedule_day,
        interval_value=data.interval_value,
        timezone=data.timezone,
        phase_mode=phase_mode,
        finalize_description=data.finalize_description,
        plan_mode=effective_plan_mode,
        plan_status=PlanStatus.PLANNING.value,
        # Inherit the owner's default at fire time, not at creation:
        # store the client value as-is and resolve the owner's CURRENT
        # default live via resolve_schedule_profile() (no snapshot of
        # current_user.default_profile_id — that freeze was the bug).
        # 'default'/None means inherit; any other value is a pin.
        ai_profile_id=data.ai_profile_id,
        created_by=current_user.id,
    )
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)

    # Register APScheduler job
    try:
        add_schedule_job(
            schedule_id=schedule.id,
            task_title=schedule.title,
            schedule_type=schedule.schedule_type,
            schedule_time=schedule.schedule_time,
            schedule_day=schedule.schedule_day,
            interval_value=schedule.interval_value,
            timezone=schedule.timezone,
            store_id=schedule.store_id,
            description=schedule.description,
            plan_mode=schedule.plan_mode,
            ai_profile_id=schedule.ai_profile_id,
            phase_mode=schedule.phase_mode,
            created_at=schedule.created_at,
        )
    except Exception as e:
        logger.exception(
            'Failed to add APScheduler job for schedule %s',
            schedule.id,
        )
        raise HTTPException(
            status_code=500,
            detail=f'Schedule created but job failed: {e}',
        )

    # Spawn the planning task for plan-mode schedules. The APScheduler
    # job is already registered, but the fire-gate refuses until the
    # plan is READY.
    if schedule_needs_plan(schedule):
        try:
            await spawn_planning_task(schedule, current_user, db)
        except Exception as e:
            logger.exception(
                'Failed to spawn planning task for schedule %s',
                schedule.id,
            )
            # Clean up the orphaned pointer + task left by a partial
            # spawn so /replan isn't tricked by the idempotency check
            # into returning a never-started task.
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

    return await _enrich_response(schedule)


@router.get('/{schedule_id}', response_model=ScheduleResponse)
async def get_schedule(
    schedule_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Get a single schedule with computed fields."""
    schedule = await db.get(Schedule, schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail='Schedule not found')
    return await _enrich_response(schedule)


@router.put('/{schedule_id}', response_model=ScheduleResponse)
async def update_schedule(
    schedule_id: str,
    data: ScheduleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a schedule and recreate its APScheduler job.

    - ``plan_mode`` is immutable after creation; changes are rejected
      (400). Toggling plan_mode off would bypass the fire-gate in
      unpredictable ways; instead, delete and recreate the schedule.
    - ``plan_version`` acts as an optimistic-lock token (If-Match
      semantics). When provided, returns 412 on mismatch.
    - A change to ``description`` (the agent prompt, normalized) on
      a plan-mode schedule invalidates the stored plan: any running
      planner task is aborted, ``plan_status`` becomes ``stale``, the
      old plan text is retained for diff display until re-plan.
    """
    schedule = await db.get(Schedule, schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail='Schedule not found')

    update_data = data.model_dump(exclude_unset=True)

    # Optimistic lock — client passes the plan_version it last saw.
    client_version = update_data.pop('plan_version', None)
    if client_version is not None and client_version != schedule.plan_version:
        raise HTTPException(
            status_code=412,
            detail=(
                f'Stale plan_version: client={client_version} '
                f'server={schedule.plan_version}'
            ),
        )

    # plan_mode is immutable — the plan lifecycle would be bypassed.
    if 'plan_mode' in update_data:
        new_plan_mode = update_data.pop('plan_mode')
        if new_plan_mode is not None and new_plan_mode != schedule.plan_mode:
            raise HTTPException(
                status_code=400,
                detail=(
                    'plan_mode is immutable after schedule creation; '
                    'delete and recreate the schedule to change it'
                ),
            )

    if 'schedule_type' in update_data:
        _validate_schedule_type(update_data['schedule_type'])
    if 'schedule_time' in update_data:
        _validate_schedule_time(update_data['schedule_time'])
    if 'timezone' in update_data:
        _validate_timezone(update_data['timezone'])
    validate_finalize_description(
        update_data.get('finalize_description'),
        schedule.store_id,
        schedule.phase_mode,
    )

    # Detect prompt change BEFORE applying (normalized compare so
    # whitespace-only edits don't spuriously invalidate).
    prompt_changed = False
    if 'description' in update_data:
        new_desc = update_data['description']
        if normalize_prompt(new_desc) != normalize_prompt(schedule.description):
            prompt_changed = True

    # Flip schedule state (plan_status=stale, clear pointer) in the
    # caller's session. The actual agent stop + Task → FAILED happens
    # AFTER commit via _finalize_pending_abort to avoid races with
    # the agent session's concurrent DB writes.
    if prompt_changed and schedule_needs_plan(schedule):
        await abort_current_planning_task(
            schedule,
            db,
            reason='Schedule prompt edited; plan invalidated.',
        )
        schedule.plan_status = PlanStatus.STALE.value
        # Clear any prior planning error — stale is about the prompt,
        # not the planner, and showing the stale error alongside the
        # new stale-banner is confusing.
        schedule.plan_error = None
        # Keep old plan text so the UI can show a diff until the
        # user re-plans.

    for key, value in update_data.items():
        setattr(schedule, key, value)
    schedule.updated_at = datetime.now(UTC).isoformat()

    await db.commit()
    await finalize_pending_abort(schedule)

    # Notify the frontend so SchedulePlanPanel + the list badge
    # refresh. Without this the UI keeps showing the old
    # plan_status until the next full page load. Mirrors the
    # `schedule_plan_ready` / `schedule_plan_timeout` events.
    if prompt_changed and schedule.plan_status == PlanStatus.STALE.value:
        await event_bus.emit(
            'schedule_plan_stale',
            {'schedule_id': schedule.id},
        )
    await db.refresh(schedule)

    # Recreate APScheduler job with new params
    remove_schedule_job(schedule.id)
    if schedule.is_active:
        try:
            add_schedule_job(
                schedule_id=schedule.id,
                task_title=schedule.title,
                schedule_type=schedule.schedule_type,
                schedule_time=schedule.schedule_time,
                schedule_day=schedule.schedule_day,
                interval_value=schedule.interval_value,
                timezone=schedule.timezone,
                store_id=schedule.store_id,
                description=schedule.description,
                plan_mode=schedule.plan_mode,
                ai_profile_id=schedule.ai_profile_id,
                phase_mode=schedule.phase_mode,
                created_at=schedule.created_at,
            )
        except Exception as e:
            logger.exception(
                'Failed to recreate job for schedule %s',
                schedule.id,
            )
            raise HTTPException(
                status_code=500,
                detail=f'Schedule updated but job failed: {e}',
            )

    return await _enrich_response(schedule)


@router.delete('/{schedule_id}')
async def delete_schedule(
    schedule_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Remove APScheduler job and delete the schedule record."""
    schedule = await db.get(Schedule, schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail='Schedule not found')

    remove_schedule_job(schedule.id)
    await db.delete(schedule)
    await db.commit()
    return {'ok': True}


@router.post('/{schedule_id}/pause', response_model=ScheduleResponse)
async def pause_schedule(
    schedule_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Set is_active=False and pause the APScheduler job."""
    schedule = await db.get(Schedule, schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail='Schedule not found')

    schedule.is_active = False
    schedule.updated_at = datetime.now(UTC).isoformat()
    await db.commit()

    pause_schedule_job(schedule.id)
    return await _enrich_response(schedule)


@router.post('/{schedule_id}/resume', response_model=ScheduleResponse)
async def resume_schedule(
    schedule_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Set is_active=True and resume the APScheduler job."""
    schedule = await db.get(Schedule, schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail='Schedule not found')

    # Fire-gate: plan-mode schedules may not resume unless plan is READY.
    # Otherwise the APScheduler job would fire and be silently rejected
    # by the gate in cron/fanout, leaving the user confused.
    if (
        schedule_needs_plan(schedule)
        and schedule.plan_status != PlanStatus.READY.value
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                f'Cannot resume schedule with plan_status='
                f'{schedule.plan_status!r}. Call /replan first.'
            ),
        )

    schedule.is_active = True
    schedule.updated_at = datetime.now(UTC).isoformat()
    await db.commit()

    # Re-add the job (in case it was removed rather than paused)
    remove_schedule_job(schedule.id)
    try:
        add_schedule_job(
            schedule_id=schedule.id,
            task_title=schedule.title,
            schedule_type=schedule.schedule_type,
            schedule_time=schedule.schedule_time,
            schedule_day=schedule.schedule_day,
            interval_value=schedule.interval_value,
            timezone=schedule.timezone,
            store_id=schedule.store_id,
            description=schedule.description,
            plan_mode=schedule.plan_mode,
            ai_profile_id=schedule.ai_profile_id,
            phase_mode=schedule.phase_mode,
            created_at=schedule.created_at,
        )
    except Exception as e:
        logger.exception('Failed to resume job for schedule %s', schedule.id)
        raise HTTPException(
            status_code=500,
            detail=f'Schedule resumed but job failed: {e}',
        )

    return await _enrich_response(schedule)


@router.get('/{schedule_id}/tasks', response_model=list[TaskResponse])
async def list_schedule_tasks(
    schedule_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """List scheduled RUN tasks (fires) for this schedule.

    Excludes plan-only tasks (``is_plan_only=True``) — those are a
    creation-time authoring artefact, not runs, and they surface
    through ``GET /{id}/plan``'s ``planning_task_history`` instead.
    Without this filter the frontend rendered them under a
    "System" group and inflated the run count.
    """
    schedule = await db.get(Schedule, schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail='Schedule not found')

    result = await db.execute(
        select(Task)
        .where(
            Task.schedule_id == schedule_id,
            Task.is_plan_only.is_(False),
        )
        .order_by(Task.created_at.desc())
    )
    return result.scalars().all()


@router.get('/{schedule_id}/plan', response_model=SchedulePlanResponse)
async def get_schedule_plan(
    schedule_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Return the plan-authoring state for a schedule.

    Used by the frontend's SchedulePlanPanel to render every state
    (ready / planning / stale / failed / none) plus a short history
    of prior plan-only Tasks. Limited to the last 10 planning tasks
    newest-first; older entries stay in the DB but aren't surfaced.
    """
    schedule = await db.get(Schedule, schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail='Schedule not found')

    result = await db.execute(
        select(Task)
        .where(
            Task.schedule_id == schedule_id,
            Task.is_plan_only.is_(True),
        )
        .order_by(Task.created_at.desc())
        .limit(10)
    )
    history = [
        SchedulePlanTaskSummary(
            id=t.id,
            status=t.status,
            created_at=t.created_at,
            completed_at=t.completed_at,
            error=t.error,
        )
        for t in result.scalars().all()
    ]

    return SchedulePlanResponse(
        plan_status=schedule.plan_status,
        plan_version=schedule.plan_version,
        plan_text=schedule.plan,
        plan_error=schedule.plan_error,
        current_planning_task_id=schedule.current_planning_task_id,
        planning_task_history=history,
        finalize_description=schedule.finalize_description,
    )


@router.post('/{schedule_id}/trigger')
async def trigger_schedule(
    schedule_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manually trigger a schedule: create a child task now.

    All-stores schedules route by ``phase_mode``:
    - ``single`` → one no-store task via ``run_single_job``
    - ``fanout`` / ``two_phase`` → one task per active store via
      ``run_fanout_job`` (two_phase handled inside the runner)
    """
    schedule = await db.get(Schedule, schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail='Schedule not found')

    # Fire-gate: plan-mode schedules must have a READY plan.
    # Exempts system/catalog schedules (is_system=True).
    if (
        schedule_needs_plan(schedule)
        and schedule.plan_status != PlanStatus.READY.value
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                f'Schedule cannot fire with plan_status='
                f'{schedule.plan_status!r}. Call /replan first.'
            ),
        )

    # All-stores schedules: route by phase_mode (mirror cron.py)
    if schedule.store_id is None:
        if schedule.phase_mode == PhaseMode.SINGLE:
            runner = run_single_job
            response_shape = 'single'
        else:
            runner = run_fanout_job
            response_shape = 'fanout'
        asyncio.create_task(
            runner(
                schedule_id=schedule.id,
                task_title=schedule.title,
                description=schedule.description,
                plan_mode=schedule.plan_mode,
                ai_profile_id=schedule.ai_profile_id,
            )
        )
        return {'ok': True, 'mode': response_shape}

    # Create child task directly (same logic as _run_task_job)
    await _cancel_prior_waiting(db, schedule.id, schedule.store_id)
    task = Task(
        store_id=schedule.store_id,
        schedule_id=schedule.id,
        created_by=current_user.id,
        title=schedule.title,
        description=schedule.description or schedule.title,
        platform=schedule.platform,
        country=schedule.country,
        status=TaskStatus.PENDING,
        plan_mode=schedule.plan_mode,
        ai_profile_id=await resolve_schedule_profile(schedule, db),
    )
    if schedule.plan:
        task.plan = schedule.plan
        task.plan_version = schedule.plan_version
        task.status = TaskStatus.PLANNED
    db.add(task)
    await db.commit()
    await db.refresh(task)

    # Emit SSE event
    await event_bus.emit(
        'schedule_triggered',
        {
            'schedule_id': schedule.id,
            'task_id': task.id,
            'title': schedule.title,
        },
    )

    # Submit to task queue if store-bound
    if schedule.store_id:
        try:
            await task_queue_scheduler.submit(task.id, schedule.store_id)
        except Exception as e:
            logger.error('Manual trigger: failed to submit task: %s', e)

    return {
        'ok': True,
        'task_id': task.id,
        'status': task.status,
    }


@router.post('/{schedule_id}/replan', response_model=ScheduleResponse)
async def replan_schedule(
    schedule_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Spawn (or re-spawn) the planning task for a schedule.

    Idempotent: if a non-terminal planning task already exists for
    this schedule we return the current response without spawning a
    duplicate. Used both for explicit re-planning after a prompt edit
    (``plan_status=stale``) and for retrying a failed plan.
    """
    schedule = await db.get(Schedule, schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail='Schedule not found')
    if not schedule_needs_plan(schedule):
        raise HTTPException(
            status_code=400,
            detail=(
                'Schedule does not use plan mode; nothing to plan. '
                'Only non-system plan_mode schedules have plans.'
            ),
        )

    # Idempotency: if a planning task is already non-terminal, reuse it.
    if schedule.current_planning_task_id:
        existing = await db.get(Task, schedule.current_planning_task_id)
        if existing and existing.status not in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
        }:
            return await _enrich_response(schedule)

    # Spawn a fresh planning task (after cleaning up any stale pointer).
    await abort_current_planning_task(
        schedule,
        db,
        reason='Superseded by /replan request.',
    )
    # _spawn_planning_task commits the caller's session, so finalize
    # the abort AFTER that commit (in its own session — see
    # _finalize_pending_abort for the session-isolation rationale).
    await spawn_planning_task(schedule, current_user, db)
    await finalize_pending_abort(schedule)
    return await _enrich_response(schedule)
