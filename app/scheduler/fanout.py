"""Trigger logic for all-stores schedules.

Three runner shapes live here, picked by ``phase_mode`` at registration
time in :func:`app.scheduler.cron.add_schedule_job`:

- :func:`run_fanout_job` — ``phase_mode='fanout'`` (default). One
  independent task per active store, linked by a shared ``batch_id``.
  Each task goes through the normal design/execute pipeline and is
  queued per-store via ``TaskQueueScheduler``.
- :func:`run_single_job` — ``phase_mode='single'``. One no-store task
  per tick. For shared work (IMAP sweep, account health, housekeeping)
  where fanning per store would be wasteful or semantically wrong.
- :func:`_run_l2_phase` + fanout — ``phase_mode='two_phase'``. Create
  a single no-store prerequisite task, await completion, then fan out
  per store. Used by catalog sync where the L2 (global) catalog must
  be rebuilt before any L3 (per-store).
"""

import asyncio
from datetime import UTC, datetime
import logging
import uuid

from sqlalchemy import select

from app import telemetry
from app.ai.profiles import resolve_schedule_profile
from app.browser.manager import store_slug as _store_slug
from app.config import DEFAULT_USER_ID
from app.database import async_session
from app.events.bus import event_bus
from app.models.schedule import Schedule
from app.models.schedule_constants import PhaseMode, StalenessCheck
from app.models.store import Store
from app.models.task import Task
from app.plan_states import PlanStatus
from app.prompts import (
    CATALOG_DESC_L2,
    CATALOG_DESC_L3,
    render_prompt,
)
from app.scheduler.task_queue import task_queue_scheduler
from app.task_states import TaskStatus, assert_transition, can_transition
from app.telemetry_events import TelemetryEvent
from app.workspace.knowledge_sync import knowledge_sync

logger = logging.getLogger(__name__)


def _schedule_can_fire(sched: Schedule) -> bool:
    """Plan-mode schedules fire only when plan_status=READY.

    System schedules and plan_mode=False schedules are always
    eligible. Mirrors ``cron._schedule_can_fire`` — kept local to
    avoid a cron.py import cycle.
    """
    if sched.is_system:
        return True
    if not sched.plan_mode:
        return True
    return sched.plan_status == PlanStatus.READY.value


async def _cancel_prior_waiting(
    db,
    schedule_id: str,
    store_id: str | None,
) -> None:
    """Cancel any non-terminal WAITING task for this (schedule, store).

    Prevents fanout stalling: silently skipping firing while the
    previous run is stuck in WAITING would block the schedule for
    the full waiting timeout (default 30 days). Instead we cancel
    the stuck task and emit a loud SSE so the user sees it.
    """
    q = select(Task).where(
        Task.schedule_id == schedule_id,
        Task.status == TaskStatus.WAITING,
    )
    if store_id is None:
        q = q.where(Task.store_id.is_(None))
    else:
        q = q.where(Task.store_id == store_id)
    result = await db.execute(q)
    prior = result.scalars().all()
    if not prior:
        return
    now = datetime_now_iso()
    cancelled_ids: list[str] = []
    for t in prior:
        if not can_transition(t.status, TaskStatus.FAILED):
            continue
        assert_transition(t.status, TaskStatus.FAILED)
        t.status = TaskStatus.FAILED
        t.error = (
            'Cancelled by next schedule fire (prior run was WAITING'
            ' on user input; cancelling so the daily schedule stays'
            ' live).'
        )
        t.error_category = 'superseded_by_next_fire'
        t.updated_at = now
        cancelled_ids.append(t.id)
    await db.commit()
    # Emit SSE only after the DB write lands — avoids clients racing
    # to refetch a WAITING task that is still WAITING in the DB.
    for tid in cancelled_ids:
        await event_bus.emit(
            'schedule_waiting_cancelled',
            {
                'schedule_id': schedule_id,
                'task_id': tid,
                'store_id': store_id,
            },
        )


def datetime_now_iso() -> str:
    """ISO-formatted UTC now — indirection so tests can monkeypatch."""
    return datetime.now(UTC).isoformat()


async def run_fanout_job(
    schedule_id: str,
    task_title: str,
    description: str | None = None,
    plan_mode: bool = False,
    ai_profile_id: str | None = 'default',
):
    """Fan-out job: create one task per active store.

    Called by APScheduler when an all-stores schedule fires.
    For schedules with ``phase_mode='two_phase'``, runs a single
    no-store prerequisite task first (awaited), then fans out.
    """
    logger.info(
        'Fan-out job triggered: %s (schedule=%s)',
        task_title,
        schedule_id,
    )

    # Load schedule for saved plan + orchestration flags
    saved_plan = None
    saved_plan_version: int | None = None
    two_phase = False
    is_catalog = False
    skip_reflection = False
    resolved_profile: str = ai_profile_id or 'default'
    async with async_session() as db:
        sched = await db.get(Schedule, schedule_id)
        if not sched:
            return
        if not _schedule_can_fire(sched):
            logger.info(
                'Fanout %s skipped: plan_status=%s plan_mode=%s is_system=%s',
                schedule_id,
                sched.plan_status,
                sched.plan_mode,
                sched.is_system,
            )
            await event_bus.emit(
                'schedule_skipped',
                {
                    'schedule_id': schedule_id,
                    'reason': 'plan_not_ready',
                    'plan_status': sched.plan_status,
                },
            )
            return
        saved_plan = sched.plan
        saved_plan_version = sched.plan_version
        two_phase = sched.phase_mode == PhaseMode.TWO_PHASE
        is_catalog = sched.staleness_check == StalenessCheck.CATALOG
        skip_reflection = sched.skip_reflection
        schedule_type = sched.schedule_type
        phase_mode_value = sched.phase_mode
        resolved_profile = await resolve_schedule_profile(sched, db)

    # Get all active stores
    async with async_session() as db:
        result = await db.execute(select(Store).order_by(Store.created_at))
        stores = result.scalars().all()

    telemetry.send(
        TelemetryEvent.SCHEDULE_FIRED,
        {
            'schedule_type': schedule_type,
            'phase_mode': phase_mode_value,
            'fanout_target_count_bucket': telemetry.count_bucket(len(stores)),
            'is_catalog': is_catalog,
        },
    )

    if not stores:
        logger.warning(
            'Fan-out job %s: no stores found, skipping',
            schedule_id,
        )
        return

    batch_id = str(uuid.uuid4())

    # ── Phase 1: prerequisite task (two-phase schedules only) ──
    if two_phase:
        await _run_l2_phase(
            schedule_id=schedule_id,
            task_title=task_title,
            plan_mode=plan_mode,
            ai_profile_id=resolved_profile,
            saved_plan=saved_plan,
            batch_id=batch_id,
            skip_reflection=skip_reflection,
        )

    # ── Phase 2: per-store fanout ──
    created_count = 0

    for store in stores:
        slug = _store_slug(store.name, store.id)
        # Catalog-specific per-store description template is gated
        # on staleness_check (the catalog-payload flag), not on
        # phase_mode (orchestration shape). A future non-catalog
        # two-phase schedule would use its own description here.
        task_description = (
            render_prompt(CATALOG_DESC_L3, store_slug=slug)
            if is_catalog
            else (description or task_title)
        )
        try:
            async with async_session() as db:
                # Cancel any prior WAITING task for this (schedule, store)
                # before firing — silent skip would stall a daily
                # schedule for up to 30 days on one forgotten question.
                await _cancel_prior_waiting(db, schedule_id, store.id)
                task = Task(
                    store_id=store.id,
                    schedule_id=schedule_id,
                    created_by=DEFAULT_USER_ID,
                    title=task_title,
                    description=task_description,
                    status=TaskStatus.PENDING,
                    plan_mode=plan_mode,
                    skip_reflection=skip_reflection,
                    ai_profile_id=resolved_profile,
                    batch_id=batch_id,
                )
                if saved_plan:
                    task.plan = saved_plan
                    task.plan_version = saved_plan_version
                    task.status = TaskStatus.PLANNED
                db.add(task)
                await db.commit()
                await db.refresh(task)

                try:
                    await task_queue_scheduler.submit(task.id, store.id)
                except Exception as e:
                    logger.error(
                        'Fan-out: failed to submit task for store %s: %s',
                        store.name,
                        e,
                    )

            created_count += 1
        except Exception:
            logger.exception(
                'Fan-out: failed to create task for store %s',
                store.name,
            )

    # Emit SSE event
    await event_bus.emit(
        'fanout_triggered',
        {
            'schedule_id': schedule_id,
            'batch_id': batch_id,
            'store_count': created_count,
            'title': task_title,
        },
    )

    logger.info(
        'Fan-out job %s: created %d tasks (batch=%s)',
        schedule_id,
        created_count,
        batch_id,
    )


async def run_single_job(
    schedule_id: str,
    task_title: str,
    description: str | None = None,
    plan_mode: bool = False,
    ai_profile_id: str | None = 'default',
):
    """Single-task job: create one no-store task per tick.

    Used by ``phase_mode='single'`` all-stores schedules whose work
    isn't per-store (shared IMAP mailbox, account health checks, etc.).
    Unlike fanout, there is no batch: exactly one Task row with
    ``store_id=None`` is created and enqueued on the no-store lane.
    """
    logger.info(
        'Single job triggered: %s (schedule=%s)',
        task_title,
        schedule_id,
    )

    saved_plan = None
    saved_plan_version: int | None = None
    skip_reflection = False
    resolved_profile: str = ai_profile_id or 'default'
    async with async_session() as db:
        sched = await db.get(Schedule, schedule_id)
        if not sched:
            return
        if not _schedule_can_fire(sched):
            logger.info(
                'Single job %s skipped: plan_status=%s plan_mode=%s',
                schedule_id,
                sched.plan_status,
                sched.plan_mode,
            )
            await event_bus.emit(
                'schedule_skipped',
                {
                    'schedule_id': schedule_id,
                    'reason': 'plan_not_ready',
                    'plan_status': sched.plan_status,
                },
            )
            return
        saved_plan = sched.plan
        saved_plan_version = sched.plan_version
        skip_reflection = sched.skip_reflection
        single_schedule_type = sched.schedule_type
        resolved_profile = await resolve_schedule_profile(sched, db)

    telemetry.send(
        TelemetryEvent.SCHEDULE_FIRED,
        {
            'schedule_type': single_schedule_type,
            'phase_mode': 'single',
            'fanout_target_count_bucket': telemetry.count_bucket(0),
            'is_catalog': False,
        },
    )

    task_id = str(uuid.uuid4())

    async with async_session() as db:
        # Cancel any prior WAITING single-task from this schedule
        # (store_id=None) so schedule doesn't stall on a forgotten
        # question.
        await _cancel_prior_waiting(db, schedule_id, None)
        task = Task(
            id=task_id,
            store_id=None,
            schedule_id=schedule_id,
            created_by=DEFAULT_USER_ID,
            title=task_title,
            description=description or task_title,
            status=TaskStatus.PENDING,
            plan_mode=plan_mode,
            skip_reflection=skip_reflection,
            ai_profile_id=resolved_profile,
        )
        if saved_plan:
            task.plan = saved_plan
            task.plan_version = saved_plan_version
            task.status = TaskStatus.PLANNED
        db.add(task)
        await db.commit()

    await event_bus.emit(
        'schedule_triggered',
        {
            'schedule_id': schedule_id,
            'task_id': task_id,
            'title': task_title,
        },
    )

    try:
        await task_queue_scheduler.submit(task_id, None)
    except Exception:
        logger.exception(
            'Single job: failed to submit task %s (schedule=%s)',
            task_id,
            schedule_id,
        )


async def _run_l2_phase(
    *,
    schedule_id: str,
    task_title: str,
    plan_mode: bool,
    ai_profile_id: str | None,
    saved_plan: str | None,
    batch_id: str,
    skip_reflection: bool = False,
) -> None:
    """Create and await the L2 (global) catalog sync task.

    Creates a no-store task, submits it through the task queue
    (same path as L3 tasks), and polls until completion.
    Catalog rotation/restore is owned entirely by auto_run_task.

    Note: this Phase 1 implementation is catalog-specific today —
    it hardcodes ``CATALOG_DESC_L2`` as the prereq description and
    ``knowledge_sync.catalog_needs_update(None)`` as the freshness
    check.  A future non-catalog ``phase_mode='two_phase'`` schedule
    would need these factored out (e.g. schedule-provided description
    template + a staleness predicate keyed on ``staleness_check``).
    No second two-phase consumer exists yet, so we avoid the
    speculative abstraction.
    """
    l2_stale, _ = knowledge_sync.catalog_needs_update(None)
    if not l2_stale:
        logger.info('L2 catalog up-to-date, skipping Phase 1')
        return

    l2_task_id = str(uuid.uuid4())

    async with async_session() as db:
        task = Task(
            id=l2_task_id,
            store_id=None,
            schedule_id=schedule_id,
            created_by=DEFAULT_USER_ID,
            title=task_title,
            description=CATALOG_DESC_L2,
            status=TaskStatus.PENDING,
            plan_mode=plan_mode,
            skip_reflection=skip_reflection,
            ai_profile_id=ai_profile_id or 'default',
            batch_id=batch_id,
        )
        if saved_plan:
            task.plan = saved_plan
            task.status = TaskStatus.PLANNED
        db.add(task)
        await db.commit()

        await event_bus.emit(
            'schedule_triggered',
            {
                'schedule_id': schedule_id,
                'task_id': l2_task_id,
                'title': task_title,
            },
        )

    # Submit through queue — single entry point for all task
    # execution, including catalog rotation/restore.
    await task_queue_scheduler.submit(l2_task_id, None)

    # Poll until terminal state (max 10 min)
    for _ in range(600):
        await asyncio.sleep(1)
        async with async_session() as db:
            t = await db.get(Task, l2_task_id)
            if t and t.status in (
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
            ):
                break

    async with async_session() as db:
        l2_task = await db.get(Task, l2_task_id)
    if not l2_task or l2_task.status != TaskStatus.COMPLETED:
        raise RuntimeError(
            f'L2 catalog sync failed: '
            f'status={l2_task.status if l2_task else "missing"}, '
            f'error={l2_task.error if l2_task else "task not found"}'
        )
