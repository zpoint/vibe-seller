"""
Cronjob scheduler using APScheduler 3.x.

Uses MemoryJobStore — the Schedule DB table is the source of truth;
APScheduler jobs are rebuilt on startup via rebuild_schedule_jobs().
"""

import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from app.ai.profiles import resolve_schedule_profile
from app.config import DEFAULT_USER_ID
from app.database import async_session
from app.events.bus import event_bus
from app.models.schedule import Schedule
from app.models.schedule_constants import PhaseMode
from app.models.task import Task
from app.plan_states import PlanStatus
from app.scheduler.email_sync import sync_all_email_accounts
from app.scheduler.fanout import (
    _cancel_prior_waiting,
    run_fanout_job,
    run_single_job,
)
from app.scheduler.finalize_reaper import reap_finalized_batches
from app.scheduler.plan_reaper import reap_stuck_planning_tasks
from app.scheduler.stall_reaper import reap_stalled_running_tasks
from app.scheduler.task_cleanup import cleanup_old_tasks
from app.scheduler.task_queue import task_queue_scheduler
from app.scheduler.waiting import check_waiting_tasks
from app.task_states import TaskStatus
from app.utils.timezone import get_server_timezone

logger = logging.getLogger(__name__)


def _create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    return scheduler


scheduler = _create_scheduler()


def start_scheduler():
    """Start the scheduler. Called during app lifespan."""
    if not scheduler.running:
        scheduler.start()
        logger.info('Scheduler started')
    # Register periodic waiting-task checker
    _register_waiting_checker()
    _register_email_sync()
    _register_plan_reaper()
    _register_stall_reaper()
    _register_finalize_reaper()
    _register_task_cleanup()


def _register_waiting_checker():
    """Add an interval job that checks waiting tasks."""
    scheduler.add_job(
        check_waiting_tasks,
        'interval',
        minutes=15,
        id='waiting_checker',
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info('Waiting-task checker registered (every 15 min)')


def _register_email_sync():
    """Add an interval job that syncs email accounts."""
    scheduler.add_job(
        sync_all_email_accounts,
        'interval',
        minutes=5,
        id='email_sync',
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info('Email sync job registered (every 5 min)')


def _register_plan_reaper():
    """Add an interval job that reaps stuck plan-only tasks."""
    scheduler.add_job(
        reap_stuck_planning_tasks,
        'interval',
        minutes=5,
        id='plan_reaper',
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info('Plan-reaper job registered (every 5 min)')


def _register_task_cleanup():
    """Add a daily job that deletes terminal tasks past retention.

    Runs at 03:30 server-local time so it falls in a low-activity
    window and isn't tied to server boot time. Hidden from the
    user — we don't insert a Schedule row.
    """
    scheduler.add_job(
        cleanup_old_tasks,
        CronTrigger(
            hour=3, minute=30, timezone=ZoneInfo(get_server_timezone())
        ),
        id='task_cleanup',
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info('Task-cleanup job registered (daily at 03:30 server time)')


def _register_stall_reaper():
    """Fail RUNNING tasks whose agent stream has gone silent.

    1-min interval keeps test + UI latency tight: a stalled task is
    reaped within 5–6 min end-to-end (5 min stall threshold + up to
    one interval to notice). Previous 2-min interval added an extra
    minute to that worst case, which under heavy parallel CI load
    pushed reap time outside the catalog-sync e2e test budget.
    """
    scheduler.add_job(
        reap_stalled_running_tasks,
        'interval',
        minutes=1,
        id='stall_reaper',
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info('Stall-reaper job registered (every 1 min)')


def _register_finalize_reaper():
    """Fire a fanout batch's parent finalize step once children are done.

    1-min interval mirrors the stall reaper: a batch whose last child
    just went terminal gets its finalize task within ~1 min. Cheap —
    a couple of indexed counts per active batch. ``max_instances=1`` +
    ``coalesce`` make it the single writer, so finalize fires once.
    """
    scheduler.add_job(
        reap_finalized_batches,
        'interval',
        minutes=1,
        id='finalize_reaper',
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info('Finalize-reaper job registered (every 1 min)')


def stop_scheduler():
    """Shutdown the scheduler. Called during app lifespan."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info('Scheduler stopped')


def _schedule_can_fire(sched: Schedule) -> bool:
    """Return True if this schedule is eligible to fire now.

    Plan-mode schedules require ``plan_status=READY``. System
    schedules (``is_system=True``) and plan_mode=False schedules
    are always eligible.
    """
    if sched.is_system:
        return True
    if not sched.plan_mode:
        return True
    return sched.plan_status == PlanStatus.READY.value


async def _run_task_job(
    task_title: str,
    store_id: str | None = None,
    schedule_id: str | None = None,
    description: str | None = None,
    plan_mode: bool = False,
    ai_profile_id: str | None = 'default',
):
    """Job function: creates a task and submits it to the queue."""
    logger.info('Cron job triggered: %s (schedule=%s)', task_title, schedule_id)

    # Pre-load saved plan from the schedule (if any)
    saved_plan = None
    saved_plan_version: int | None = None
    platform = None
    country = None
    # Inherit the owner's current default unless the schedule
    # explicitly pins a provider (see resolve_schedule_profile).
    resolved_profile: str = ai_profile_id or 'default'
    if schedule_id:
        async with async_session() as db:
            sched = await db.get(Schedule, schedule_id)
            if sched:
                # Fire-gate: plan-mode schedules must have plan_status
                # READY. System schedules (catalog sync) and
                # plan_mode=False schedules are exempt.
                if not _schedule_can_fire(sched):
                    logger.info(
                        'Schedule %s fire skipped: plan_status=%s'
                        ' plan_mode=%s is_system=%s',
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
                platform = sched.platform
                country = sched.country
                resolved_profile = await resolve_schedule_profile(sched, db)

    async with async_session() as db:
        if schedule_id:
            # Cancel-forward any prior WAITING task for this
            # (schedule, store) so a single forgotten question
            # doesn't stall the whole schedule.
            await _cancel_prior_waiting(db, schedule_id, store_id)
        task = Task(
            store_id=store_id,
            schedule_id=schedule_id,
            created_by=DEFAULT_USER_ID,
            title=task_title,
            description=description or task_title,
            platform=platform,
            country=country,
            status=TaskStatus.PENDING,
            plan_mode=plan_mode,
            ai_profile_id=resolved_profile,
        )
        # If the schedule has a saved plan, pre-load it so the
        # child task can skip the design phase.
        if saved_plan:
            task.plan = saved_plan
            task.plan_version = saved_plan_version
            task.status = TaskStatus.PLANNED
        db.add(task)
        await db.commit()
        await db.refresh(task)

        # Emit SSE event
        await event_bus.emit(
            'schedule_triggered',
            {
                'schedule_id': schedule_id or '',
                'task_id': task.id,
                'title': task_title,
            },
        )

        if store_id:
            try:
                await task_queue_scheduler.submit(task.id, store_id)
            except Exception as e:
                logger.error(
                    'Cron job: failed to submit task to queue: %s',
                    e,
                )


def build_trigger(
    schedule_type: str,
    schedule_time: str,
    schedule_day: int | None = None,
    interval_value: int = 1,
    timezone: str | None = None,
) -> CronTrigger | IntervalTrigger:
    """Build an APScheduler trigger from schedule params.

    Returns CronTrigger for weekly/monthly, IntervalTrigger for
    minutes/hours/days.
    """
    tz = ZoneInfo(timezone or get_server_timezone())

    if schedule_type == 'minutes':
        return IntervalTrigger(minutes=max(1, interval_value), timezone=tz)
    if schedule_type == 'hours':
        return IntervalTrigger(hours=max(1, interval_value), timezone=tz)
    if schedule_type == 'days':
        parts = schedule_time.split(':')
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        if interval_value == 1:
            # Exact daily at HH:MM via cron
            return CronTrigger(hour=hour, minute=minute, timezone=tz)
        return IntervalTrigger(days=interval_value, timezone=tz)
    if schedule_type == 'weekly':
        parts = schedule_time.split(':')
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        kwargs: dict = {
            'hour': hour,
            'minute': minute,
            'timezone': tz,
        }
        if schedule_day is not None:
            # DB stores ISO weekday (Mon=1..Sun=7) but APScheduler's
            # CronTrigger uses Mon=0..Sun=6 — translate at the boundary.
            # Range-check explicitly so bad input fails loudly instead of
            # being silently wrapped to a different weekday.
            if not 1 <= schedule_day <= 7:
                raise ValueError(
                    f'weekly schedule_day must be 1..7 (ISO weekday, '
                    f'Mon=1..Sun=7); got {schedule_day}'
                )
            kwargs['day_of_week'] = schedule_day - 1
        return CronTrigger(**kwargs)
    if schedule_type == 'monthly':
        parts = schedule_time.split(':')
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        kwargs = {
            'hour': hour,
            'minute': minute,
            'timezone': tz,
        }
        if schedule_day is not None:
            kwargs['day'] = schedule_day
        return CronTrigger(**kwargs)

    # Fallback: daily
    parts = schedule_time.split(':')
    return CronTrigger(
        hour=int(parts[0]),
        minute=int(parts[1]) if len(parts) > 1 else 0,
        timezone=tz,
    )


def add_cron_job(
    job_id: str,
    task_title: str,
    cron_expression: str,
    store_id: str | None = None,
) -> dict:
    """Add a new cron job from a raw cron expression.

    cron_expression format: "minute hour day month day_of_week"
    e.g., "0 9 * * 1-5" = 9 AM weekdays
    """
    parts = cron_expression.strip().split()
    if len(parts) != 5:
        raise ValueError(
            f'Invalid cron expression (need 5 fields): {cron_expression}'
        )

    trigger = CronTrigger(
        minute=parts[0],
        hour=parts[1],
        day=parts[2],
        month=parts[3],
        day_of_week=parts[4],
    )

    scheduler.add_job(
        _run_task_job,
        trigger=trigger,
        id=job_id,
        name=task_title,
        kwargs={'task_title': task_title, 'store_id': store_id},
        replace_existing=True,
    )
    logger.info(
        "Cron job added: %s = '%s' @ %s",
        job_id,
        task_title,
        cron_expression,
    )

    return {
        'job_id': job_id,
        'cron': cron_expression,
        'task_title': task_title,
    }


def add_schedule_job(
    schedule_id: str,
    task_title: str,
    schedule_type: str,
    schedule_time: str,
    schedule_day: int | None = None,
    interval_value: int = 1,
    timezone: str | None = None,
    store_id: str | None = None,
    description: str | None = None,
    plan_mode: bool = False,
    ai_profile_id: str | None = 'default',
    phase_mode: str = 'fanout',
) -> None:
    """Add an APScheduler job for a Schedule record.

    ``phase_mode`` is only consulted when ``store_id is None``. Values:
    - ``'fanout'`` / ``'two_phase'``: route to ``run_fanout_job``
      (two_phase handled inside the fanout runner).
    - ``'single'``: route to ``run_single_job`` — one no-store task
      per tick, no fan-out.
    """
    trigger = build_trigger(
        schedule_type,
        schedule_time,
        schedule_day,
        interval_value,
        timezone or get_server_timezone(),
    )

    job_id = f'schedule_{schedule_id}'

    if store_id is None:
        if phase_mode == PhaseMode.SINGLE:
            runner = run_single_job
        else:
            runner = run_fanout_job
        scheduler.add_job(
            runner,
            trigger=trigger,
            id=job_id,
            name=task_title,
            kwargs={
                'schedule_id': schedule_id,
                'task_title': task_title,
                'description': description,
                'plan_mode': plan_mode,
                'ai_profile_id': ai_profile_id,
            },
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    else:
        scheduler.add_job(
            _run_task_job,
            trigger=trigger,
            id=job_id,
            name=task_title,
            kwargs={
                'task_title': task_title,
                'store_id': store_id,
                'schedule_id': schedule_id,
                'description': description,
                'plan_mode': plan_mode,
                'ai_profile_id': ai_profile_id,
            },
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    logger.info(
        'Schedule job added: %s (%s) type=%s interval=%d time=%s',
        job_id,
        task_title,
        schedule_type,
        interval_value,
        schedule_time,
    )


def remove_schedule_job(schedule_id: str) -> bool:
    """Remove the APScheduler job for a schedule."""
    job_id = f'schedule_{schedule_id}'
    try:
        scheduler.remove_job(job_id)
        return True
    except Exception:
        return False


def pause_schedule_job(schedule_id: str) -> bool:
    """Pause the APScheduler job for a schedule."""
    job_id = f'schedule_{schedule_id}'
    try:
        scheduler.pause_job(job_id)
        return True
    except Exception:
        return False


def resume_schedule_job(schedule_id: str) -> bool:
    """Resume the APScheduler job for a schedule."""
    job_id = f'schedule_{schedule_id}'
    try:
        scheduler.resume_job(job_id)
        return True
    except Exception:
        return False


def get_schedule_next_run(schedule_id: str) -> str | None:
    """Get the next run time for a schedule's APScheduler job.

    Returns None when the scheduler hasn't started (jobs added
    tentatively don't yet carry ``next_run_time``) — using getattr
    keeps the CRUD endpoints functional in test environments where
    the scheduler isn't running.
    """
    job_id = f'schedule_{schedule_id}'
    job = scheduler.get_job(job_id)
    next_run = getattr(job, 'next_run_time', None) if job else None
    return str(next_run) if next_run else None


async def rebuild_schedule_jobs():
    """Query all active schedules and re-add APScheduler jobs.

    Called on startup after the scheduler is running.
    """
    async with async_session() as db:
        result = await db.execute(
            select(Schedule).where(Schedule.is_active.is_(True))
        )
        schedules = result.scalars().all()

    count = 0
    for sched in schedules:
        try:
            add_schedule_job(
                schedule_id=sched.id,
                task_title=sched.title,
                schedule_type=sched.schedule_type,
                schedule_time=sched.schedule_time,
                schedule_day=sched.schedule_day,
                interval_value=sched.interval_value,
                timezone=sched.timezone,
                store_id=sched.store_id,
                description=sched.description,
                plan_mode=sched.plan_mode,
                ai_profile_id=sched.ai_profile_id,
                phase_mode=sched.phase_mode,
            )
            count += 1
        except Exception:
            logger.exception('Failed to rebuild job for schedule %s', sched.id)

    logger.info('Rebuilt %d schedule jobs on startup', count)


def remove_cron_job(job_id: str) -> bool:
    """Remove a cron job by ID."""
    try:
        scheduler.remove_job(job_id)
        return True
    except Exception:
        return False


def pause_cron_job(job_id: str) -> bool:
    """Pause a cron job."""
    try:
        scheduler.pause_job(job_id)
        return True
    except Exception:
        return False


def resume_cron_job(job_id: str) -> bool:
    """Resume a paused cron job."""
    try:
        scheduler.resume_job(job_id)
        return True
    except Exception:
        return False


def list_cron_jobs() -> list[dict]:
    """List all cron jobs with their status."""
    jobs = scheduler.get_jobs()
    result = []
    for job in jobs:
        result.append({
            'job_id': job.id,
            'name': job.name,
            'next_run': (str(job.next_run_time) if job.next_run_time else None),
            'paused': job.next_run_time is None,
            'trigger': str(job.trigger),
        })
    return result
