"""Periodic checker for tasks in WAITING status.

Runs on an APScheduler interval, inspects each waiting task's
``wait_condition`` JSON, and either wakes, times-out, or skips the
task depending on strategy, timing, and external signals (e.g. email).

It owns every WAITING row, so it is also the backstop for a parent
waiting on its children: whichever path ended the last child, the
parent completes here at the latest.
"""

import asyncio
from datetime import UTC, datetime, timedelta
import json
import logging

from sqlalchemy import select, update

from app.ai.question_policy import waits_on_a_person
from app.database import async_session
from app.email.db import search_emails
from app.events.bus import event_bus
from app.models.email_account import EmailAccount
from app.models.store_email_link import StoreEmailLink
from app.models.task import Task
from app.scheduler.task_queue import task_queue_scheduler
from app.task_finalize import check_parent_after_run
from app.task_runner import complete_if_children_done
from app.task_states import TaskStatus
from app.task_status_cas import transition_if
from app.telemetry_tasks import send_task_failed

logger = logging.getLogger(__name__)

# Default maximum days a task can stay in WAITING before timeout.
_DEFAULT_MAX_WAIT_DAYS = 30

#: ``Task.error_category`` of a wait that reached its deadline.
WAIT_TIMEOUT = 'wait_timeout'


def wait_deadline(condition: dict) -> datetime | None:
    """When a wait ends unmet: ``waiting_since`` + ``max_wait_days``.

    ONE definition of the deadline: the checker fails the task at it,
    and the SPA counts its "days left" down to it
    (``frontend/src/lib/waitCondition.ts``). None for a wait with no
    clock (a parent waiting on its children).
    """
    since = condition.get('waiting_since')
    if not since:
        return None
    try:
        start = datetime.fromisoformat(since)
        days = float(condition.get('max_wait_days', _DEFAULT_MAX_WAIT_DAYS))
    except (TypeError, ValueError):
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    return start + timedelta(days=days)


def timeout_error(condition: dict) -> str:
    """``Task.error`` for a wait that reached its deadline."""
    days = condition.get('max_wait_days', _DEFAULT_MAX_WAIT_DAYS)
    unmet = (
        'Nobody replied'
        if waits_on_a_person(condition)
        else 'The wait condition was not met'
    )
    return (
        f'{unmet} within {days} days, so the task stopped. Send a '
        'message below to continue: the agent picks up where it '
        'stopped (Retry would start over).'
    )


async def check_waiting_tasks() -> None:
    """Query all WAITING tasks and evaluate their conditions.

    Intended to be called periodically by APScheduler.
    """
    async with async_session() as db:
        result = await db.execute(
            select(Task).where(Task.status == TaskStatus.WAITING)
        )
        tasks = result.scalars().all()

    for task in tasks:
        try:
            await _check_one_task(task)
        except Exception:
            logger.exception('Error checking waiting task %s', task.id)


async def _check_one_task(task: Task) -> None:
    """Evaluate a single waiting task's condition."""
    now = datetime.now(UTC)

    # Parse wait_condition JSON
    condition: dict = {}
    if task.wait_condition:
        try:
            condition = json.loads(task.wait_condition)
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                'Invalid wait_condition JSON for task %s',
                task.id,
            )
            return

    if condition.get('strategy') == 'children':
        # Ends when the last child is terminal, never on a clock.
        async with async_session() as db:
            await complete_if_children_done(await db.get(Task, task.id), db)
        return

    # ── Timeout check ───────────────────────────────────────
    deadline = wait_deadline(condition)
    if deadline is not None and now >= deadline:
        await _timeout_task(task, condition)
        return

    # ── Strategy gate ───────────────────────────────────────
    strategy = condition.get('check_strategy', 'manual')
    if strategy == 'manual':
        # Only a user action can wake this task.
        return

    # ── Timing gate ─────────────────────────────────────────
    next_check_str = condition.get('next_check_at')
    if next_check_str:
        next_check_at = datetime.fromisoformat(next_check_str)
        if now < next_check_at:
            return

    # ── Strategy dispatch ───────────────────────────────────
    matched: list[dict] | None = None

    if strategy == 'email':
        matched = await _check_email_condition(task, condition)

    # ── Outcome ─────────────────────────────────────────────
    if matched:
        await _wake_task(task, trigger_data={'emails': matched})
    else:
        # Nothing matched — update check timestamps and retry later.
        check_interval_hours = condition.get('check_interval_hours', 24)
        condition['last_checked_at'] = now.isoformat()
        condition['next_check_at'] = (
            now + timedelta(hours=check_interval_hours)
        ).isoformat()

        # Only onto the row as read: never over an answer, a wake or a
        # Stop that landed meanwhile.
        async with async_session() as db:
            await db.execute(
                update(Task)
                .where(
                    Task.id == task.id,
                    Task.status == TaskStatus.WAITING,
                    Task.wait_condition == task.wait_condition,
                )
                .values(
                    wait_condition=json.dumps(condition),
                    updated_at=now.isoformat(),
                )
            )
            await db.commit()


async def _check_email_condition(
    task: Task,
    condition: dict,
) -> list[dict] | None:
    """Query local email SQLite DBs for keyword matches.

    No network I/O — reads from per-account SQLite DBs that
    are synced in the background every 5 minutes.
    """
    if not task.store_id:
        logger.warning(
            'Task %s has email strategy but no store_id',
            task.id,
        )
        return None

    keywords: list[str] = condition.get('keywords', [])
    if not keywords:
        return None

    waiting_since = condition.get(
        'waiting_since',
        (datetime.now(UTC) - timedelta(days=7)).isoformat(),
    )

    # Get linked accounts
    async with async_session() as db:
        result = await db.execute(
            select(StoreEmailLink, EmailAccount)
            .join(
                EmailAccount,
                StoreEmailLink.email_account_id == EmailAccount.id,
            )
            .where(StoreEmailLink.store_id == task.store_id)
        )
        rows = result.all()

    if not rows:
        return None

    matched: list[dict] = []
    for _link, account in rows:
        try:
            results = await asyncio.to_thread(
                search_emails,
                account.id,
                waiting_since,
                keywords,
            )
            matched.extend(results)
        except Exception:
            logger.exception(
                'Email DB query failed for %s (task %s)',
                account.email,
                task.id,
            )

    return matched if matched else None


async def _wake_task(
    task: Task,
    trigger_data: dict | None = None,
) -> None:
    """Transition a task from WAITING to QUEUED."""
    now = datetime.now(UTC)

    # Update the wait_condition with wake metadata.
    condition: dict = {}
    if task.wait_condition:
        try:
            condition = json.loads(task.wait_condition)
        except (json.JSONDecodeError, TypeError):
            pass

    condition['woken_at'] = now.isoformat()
    if trigger_data is not None:
        condition['trigger_data'] = trigger_data

    async with async_session() as db:
        moved = await transition_if(
            db,
            task.id,
            [TaskStatus.WAITING],
            TaskStatus.QUEUED,
            where=[Task.wait_condition == task.wait_condition],
            wait_condition=json.dumps(condition),
            updated_at=now.isoformat(),
        )
    if not moved:
        # Answered, woken or stopped since this checker read it.
        return

    await event_bus.emit(
        'task_update',
        {'task_id': task.id, 'status': TaskStatus.QUEUED},
    )

    # Submit to the task queue so it gets dispatched — store-less tasks
    # too, on the ``None`` lane: QUEUED with no submit is an orphan.
    await task_queue_scheduler.submit(task.id, task.store_id)

    logger.info('Woke waiting task %s', task.id)


async def _timeout_task(task: Task, condition: dict) -> None:
    """WAITING -> FAILED at the deadline, through the doors every other
    end of a run goes through.

    * ONE conditional write on the row as this checker read it (status
      AND ``wait_condition``): an answer, a wake or a Stop that landed
      since wins, and a task that was answered, ran and parked again
      (a fresh wait) is not ended by the old wait's deadline.
    * The parent of a sub-task is re-checked, as after every run
      (``check_parent_after_run``): without it a parent waited on its
      timed-out child forever.
    """
    error = timeout_error(condition)
    async with async_session() as db:
        moved = await transition_if(
            db,
            task.id,
            [TaskStatus.WAITING],
            TaskStatus.FAILED,
            where=[Task.wait_condition == task.wait_condition],
            error=error,
            error_category=WAIT_TIMEOUT,
        )
        failed = await db.get(Task, task.id) if moved else None
    if failed is None:
        logger.info('Waiting task %s moved on before its timeout', task.id)
        return

    await event_bus.emit(
        'task_update',
        {
            'task_id': task.id,
            'status': TaskStatus.FAILED,
            'error': error,
            'error_category': WAIT_TIMEOUT,
        },
    )
    await check_parent_after_run(task.id)
    await send_task_failed(failed, phase='waiting', category=WAIT_TIMEOUT)
    logger.info('Timed out waiting task %s', task.id)
