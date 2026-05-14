"""
Task Queue Scheduler — concurrent per-store execution with session-aware scheduling.

Tasks run concurrently by default; only queued when they share the same platform
but target different countries (Ziniao country-switch constraint).

Scheduling rules:
- No running tasks for a store → RUN (start browser, dispatch)
- Session running, same platform+country OR different platform → RUN_IN_NEW_TAB
- Same platform, different country → QUEUE (Ziniao needs to switch country)
- Once a task is blocked by a country conflict, subsequent same-platform tasks
  for that store also wait until the conflict resolves.
"""

import asyncio
from datetime import UTC, datetime
import enum
import json
import logging

from sqlalchemy import select

from app.browser.manager import browser_manager
from app.database import async_session
from app.events.bus import event_bus
from app.models.browser_session import BrowserSession
from app.models.store import Store
from app.models.task import Task
from app.task_runner_auto import auto_run_task
from app.task_runner_exec import execute_planned_task, execute_woken_task
from app.task_states import TaskStatus

logger = logging.getLogger(__name__)


class ScheduleDecision(enum.Enum):
    RUN = 'run'
    RUN_IN_NEW_TAB = 'run_in_tab'
    QUEUE = 'queue'


class TaskQueueScheduler:
    def __init__(self):
        # store_id -> [task_id, ...]; None key = no-store tasks
        self._queues: dict[str | None, list[str]] = {}
        # store_id -> {task_id, ...}
        self._running_tasks: dict[str | None, set[str]] = {}
        self._lock = asyncio.Lock()
        self._tick_event = asyncio.Event()
        self._tick_task: asyncio.Task | None = None

    @property
    def is_running(self) -> bool:
        """True if the tick loop is active."""
        return self._tick_task is not None and not self._tick_task.done()

    async def start(self):
        """Recover state from DB and start the tick loop."""
        await self._recover_from_db()
        self._tick_task = asyncio.create_task(self._tick_loop())
        logger.info('TaskQueueScheduler started')

    async def stop(self):
        """Cancel the tick loop."""
        if self._tick_task:
            self._tick_task.cancel()
            try:
                await self._tick_task
            except asyncio.CancelledError:
                pass
            self._tick_task = None
        logger.info('TaskQueueScheduler stopped')

    async def submit(self, task_id: str, store_id: str | None):
        """Enqueue a task and signal the tick loop.

        Only sets status to QUEUED from PENDING/WAITING — other
        states (e.g. PLANNED being submitted for execution) are
        left untouched so the dispatcher can transition them
        correctly.
        """
        async with self._lock:
            if store_id not in self._queues:
                self._queues[store_id] = []
            self._queues[store_id].append(task_id)

        # Only transition to QUEUED from states where it
        # makes sense.  PLANNED tasks stay PLANNED so
        # _approve_plan_request can transition them to
        # RUNNING correctly.
        async with async_session() as db:
            task = await db.get(Task, task_id)
            if task and task.status in (
                TaskStatus.PENDING,
                TaskStatus.WAITING,
            ):
                task.status = TaskStatus.QUEUED
                task.updated_at = datetime.now(UTC).isoformat()
                await db.commit()
                await event_bus.emit(
                    'task_update',
                    {
                        'task_id': task_id,
                        'status': TaskStatus.QUEUED,
                    },
                )

        self._tick_event.set()

    async def cancel(self, task_id: str, store_id: str | None):
        """Remove a task from the queue (if still queued)."""
        async with self._lock:
            queue = self._queues.get(store_id, [])
            if task_id in queue:
                queue.remove(task_id)

    async def notify_task_complete(self, task_id: str, store_id: str | None):
        """Called when a task finishes (success or failure). Cleans up and signals tick."""
        async with self._lock:
            running = self._running_tasks.get(store_id, set())
            running.discard(task_id)

        # Update browser session tracking (store tasks only)
        if store_id:
            await self._update_session_tracking(store_id)

        self._tick_event.set()

    async def can_schedule(
        self, task_id: str, store_id: str | None
    ) -> ScheduleDecision:
        """Determine if a task can run now based on current store session state."""
        if not store_id:
            return ScheduleDecision.RUN

        running = self._running_tasks.get(store_id, set())
        if not running:
            return ScheduleDecision.RUN

        # Check session compatibility
        async with async_session() as db:
            task = await db.get(Task, task_id)
            if not task:
                return ScheduleDecision.QUEUE

            result = await db.execute(
                select(BrowserSession).where(
                    BrowserSession.store_id == store_id
                )
            )
            session = result.scalar_one_or_none()

            # No platform/country on task = compatible with any session
            if not task.platform and not task.country:
                return ScheduleDecision.RUN_IN_NEW_TAB

            if not session or session.status != 'running':
                # No active browser session → no country
                # conflict possible, safe to run concurrently.
                return ScheduleDecision.RUN_IN_NEW_TAB

            task_platform = (task.platform or '').lower()
            task_country = (task.country or '').lower()
            session_platform = (session.current_platform or '').lower()
            session_country = (session.current_country or '').lower()

            if not session_platform and not session_country:
                return ScheduleDecision.RUN_IN_NEW_TAB

            # Different platform → concurrent (separate portals)
            if task_platform != session_platform:
                return ScheduleDecision.RUN_IN_NEW_TAB

            # Same platform, same country → concurrent
            # (CDPMuxProxy)
            if task_country == session_country:
                return ScheduleDecision.RUN_IN_NEW_TAB

            # Same platform, different country → queue
            # (Ziniao needs to switch country within the
            # platform profile)
            return ScheduleDecision.QUEUE

    async def _tick(self):
        """Process all store queues, dispatching tasks that can run."""
        async with self._lock:
            store_ids = list(self._queues.keys())

        for store_id in store_ids:
            async with self._lock:
                queue = self._queues.get(store_id, [])
                if not queue:
                    continue

            # Process queue: check head for scheduling eligibility
            while True:
                async with self._lock:
                    queue = self._queues.get(store_id, [])
                    if not queue:
                        break
                    task_id = queue[0]

                decision = await self.can_schedule(task_id, store_id)

                if decision == ScheduleDecision.QUEUE:
                    # Head is blocked → entire store queue waits
                    break

                # RUN or RUN_IN_NEW_TAB → dispatch
                async with self._lock:
                    queue = self._queues.get(store_id, [])
                    if queue and queue[0] == task_id:
                        queue.pop(0)
                    else:
                        break
                    if store_id not in self._running_tasks:
                        self._running_tasks[store_id] = set()
                    self._running_tasks[store_id].add(task_id)

                await self._dispatch(task_id, store_id)

    async def _tick_loop(self):
        """Wait for signal or timeout, then run tick."""
        while True:
            try:
                await asyncio.wait_for(self._tick_event.wait(), timeout=10)
            except TimeoutError:
                pass
            self._tick_event.clear()
            try:
                await self._tick()
            except Exception:
                logger.exception('Error in task queue tick')

    async def _dispatch(self, task_id: str, store_id: str | None):
        """Start browser session if needed and launch task execution."""
        try:
            store = None
            is_wakeup = False

            async with async_session() as db:
                task = await db.get(Task, task_id)
                if task and task.wait_condition:
                    try:
                        cond = json.loads(task.wait_condition)
                        is_wakeup = 'woken_at' in cond
                    except (json.JSONDecodeError, TypeError):
                        pass

                if store_id:
                    store = await db.get(Store, store_id)
                    if not store:
                        logger.error(
                            'Store %s not found for task %s',
                            store_id,
                            task_id,
                        )
                        await self._mark_failed(task_id, 'Store not found')
                        async with self._lock:
                            self._running_tasks.get(store_id, set()).discard(
                                task_id
                            )
                        return
                    await browser_manager.write_browser_config_for_store(
                        store, db
                    )

            # Update session tracking with task's platform/country
            if store_id:
                await self._update_session_tracking_for_task(task_id, store_id)

            if is_wakeup:
                asyncio.create_task(
                    self._execute_woken_and_notify(task_id, store_id)
                )
            elif task and task.plan:
                asyncio.create_task(
                    self._execute_planned_and_notify(task_id, store_id)
                )
            else:
                asyncio.create_task(
                    self._auto_run_and_notify(task_id, store_id)
                )

        except Exception as e:
            logger.exception('Failed to dispatch task %s: %s', task_id, e)
            await self._mark_failed(task_id, str(e))
            async with self._lock:
                self._running_tasks.get(store_id, set()).discard(task_id)
            self._tick_event.set()

    async def _auto_run_and_notify(self, task_id: str, store_id: str | None):
        """Run the full plan-then-execute pipeline."""
        try:
            async with async_session() as db:
                store = await db.get(Store, store_id)
            await auto_run_task(task_id, store)
        except Exception:
            logger.exception('Task %s execution error', task_id)
        finally:
            await self.notify_task_complete(task_id, store_id)

    async def _execute_planned_and_notify(
        self, task_id: str, store_id: str | None
    ):
        """Execute a task that already has a plan."""
        try:
            async with async_session() as db:
                store = await db.get(Store, store_id)
            await execute_planned_task(task_id, store)
        except Exception:
            logger.exception('Planned task %s execution error', task_id)
        finally:
            await self.notify_task_complete(task_id, store_id)

    async def _execute_woken_and_notify(
        self, task_id: str, store_id: str | None
    ):
        """Execute a woken waiting task."""
        try:
            async with async_session() as db:
                store = await db.get(Store, store_id)
            await execute_woken_task(task_id, store)
        except Exception:
            logger.exception('Woken task %s execution error', task_id)
        finally:
            await self.notify_task_complete(task_id, store_id)

    async def _recover_from_db(self):
        """Rebuild state on startup: re-queue 'queued' tasks, mark 'running' as failed.

        Waiting tasks are left alone — the periodic checker will
        resume monitoring them once APScheduler starts.
        """
        async with async_session() as db:
            # Mark running/designing tasks as failed (agent gone
            # after restart). Do NOT touch waiting tasks — they
            # persist across restarts.
            result = await db.execute(
                select(Task).where(
                    Task.status.in_([
                        TaskStatus.RUNNING,
                        TaskStatus.DESIGNING,
                    ])
                )
            )
            for task in result.scalars().all():
                task.status = TaskStatus.FAILED
                task.error = 'Server restarted while task was active'
                task.error_category = 'server_restart'
                task.updated_at = datetime.now(UTC).isoformat()

            # Re-queue tasks that were queued or pending
            # (pending tasks may have been left behind if server
            # restarted before auto_run_task picked them up)
            result = await db.execute(
                select(Task)
                .where(
                    Task.status.in_([
                        TaskStatus.QUEUED,
                        TaskStatus.PENDING,
                    ])
                )
                .order_by(Task.created_at)
            )
            for task in result.scalars().all():
                if task.store_id:
                    if task.store_id not in self._queues:
                        self._queues[task.store_id] = []
                    self._queues[task.store_id].append(task.id)

            # Reset all browser sessions to idle
            result = await db.execute(select(BrowserSession))
            for session in result.scalars().all():
                session.status = 'idle'
                session.current_platform = None
                session.current_country = None
                session.active_tab_count = 0
                session.cdp_port = None
                session.chrome_pid = None
                session.updated_at = datetime.now(UTC).isoformat()

            await db.commit()

        queued_count = sum(len(q) for q in self._queues.values())
        if queued_count:
            logger.info(f'Recovered {queued_count} queued tasks from DB')
            self._tick_event.set()

    async def _update_session_tracking(self, store_id: str):
        """Update browser session tracking fields based on currently running tasks."""
        async with self._lock:
            running = self._running_tasks.get(store_id, set())

        async with async_session() as db:
            result = await db.execute(
                select(BrowserSession).where(
                    BrowserSession.store_id == store_id
                )
            )
            session = result.scalar_one_or_none()
            if not session:
                return

            if not running:
                session.current_platform = None
                session.current_country = None
                session.active_tab_count = 0
            else:
                session.active_tab_count = len(running)

            session.updated_at = datetime.now(UTC).isoformat()
            await db.commit()

    async def _update_session_tracking_for_task(
        self, task_id: str, store_id: str
    ):
        """Set session platform/country from the task being dispatched."""
        async with async_session() as db:
            task = await db.get(Task, task_id)
            if not task:
                return

            result = await db.execute(
                select(BrowserSession).where(
                    BrowserSession.store_id == store_id
                )
            )
            session = result.scalar_one_or_none()
            if not session:
                return

            # Only set platform/country if the task specifies them and session has none
            if task.platform and not session.current_platform:
                session.current_platform = task.platform
            if task.country and not session.current_country:
                session.current_country = task.country

            async with self._lock:
                running = self._running_tasks.get(store_id, set())
            session.active_tab_count = len(running)
            session.updated_at = datetime.now(UTC).isoformat()
            await db.commit()

    async def _mark_failed(self, task_id: str, error: str):
        """Mark a task as failed."""
        async with async_session() as db:
            task = await db.get(Task, task_id)
            if task:
                task.status = TaskStatus.FAILED
                task.error = error
                task.updated_at = datetime.now(UTC).isoformat()
                await db.commit()

        await event_bus.emit(
            'task_update',
            {
                'task_id': task_id,
                'status': TaskStatus.FAILED,
                'error': error,
            },
        )


# Singleton
task_queue_scheduler = TaskQueueScheduler()
