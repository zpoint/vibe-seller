"""Periodic checker for tasks in WAITING status.

Runs on an APScheduler interval, inspects each waiting task's
``wait_condition`` JSON, and either wakes, times-out, or skips the
task depending on strategy, timing, and external signals (e.g. email).
"""

import asyncio
from datetime import UTC, datetime, timedelta
import json
import logging

from sqlalchemy import select

from app.database import async_session
from app.email.db import search_emails
from app.events.bus import event_bus
from app.models.email_account import EmailAccount
from app.models.store_email_link import StoreEmailLink
from app.models.task import Task
from app.scheduler.task_queue import task_queue_scheduler
from app.task_states import TaskStatus

logger = logging.getLogger(__name__)

# Default maximum days a task can stay in WAITING before timeout.
_DEFAULT_MAX_WAIT_DAYS = 30


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

    # ── Timeout check ───────────────────────────────────────
    max_wait_days = condition.get('max_wait_days', _DEFAULT_MAX_WAIT_DAYS)
    waiting_since_str = condition.get('waiting_since')
    if waiting_since_str:
        waiting_since = datetime.fromisoformat(waiting_since_str)
        if (now - waiting_since).days > max_wait_days:
            await _timeout_task(task)
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

        async with async_session() as db:
            t = await db.get(Task, task.id)
            if t:
                t.wait_condition = json.dumps(condition)
                t.updated_at = now.isoformat()
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
        t = await db.get(Task, task.id)
        if not t:
            return
        t.status = TaskStatus.QUEUED
        t.wait_condition = json.dumps(condition)
        t.updated_at = now.isoformat()
        await db.commit()

    await event_bus.emit(
        'task_update',
        {'task_id': task.id, 'status': TaskStatus.QUEUED},
    )

    # Submit to the task queue so it gets dispatched.
    if task.store_id:
        await task_queue_scheduler.submit(task.id, task.store_id)

    logger.info('Woke waiting task %s', task.id)


async def _timeout_task(task: Task) -> None:
    """Mark a waiting task as FAILED due to timeout."""
    now = datetime.now(UTC)
    error_msg = (
        'Task timed out while waiting. '
        'The wait condition was not met within the allowed period.'
    )

    async with async_session() as db:
        t = await db.get(Task, task.id)
        if not t:
            return
        t.status = TaskStatus.FAILED
        t.error = error_msg
        t.updated_at = now.isoformat()
        await db.commit()

    await event_bus.emit(
        'task_update',
        {
            'task_id': task.id,
            'status': TaskStatus.FAILED,
            'error': error_msg,
        },
    )

    logger.info('Timed out waiting task %s', task.id)
