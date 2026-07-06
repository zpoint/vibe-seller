"""Unit tests for TaskQueueScheduler — submit, dispatch, recovery,
and can_schedule scheduling decisions."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.browser_session import BrowserSession
from app.models.store import Store
from app.models.task import Task
from app.scheduler.task_queue import ScheduleDecision, TaskQueueScheduler
from app.task_states import TaskStatus

pytestmark = pytest.mark.unit


@pytest.fixture
async def db_session():
    """In-memory SQLite for isolated queue tests."""
    engine = create_async_engine(
        'sqlite+aiosqlite://',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    yield maker

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def store_and_task(db_session):
    """Create a store + task for queue tests."""

    async def _create(
        task_id='task-1',
        status=TaskStatus.PENDING,
        plan_mode=False,
        plan=None,
        platform=None,
        country=None,
        schedule_id=None,
    ):
        async with db_session() as db:
            # Only create store if it doesn't exist yet
            existing = await db.get(Store, 'store-1')
            if not existing:
                store = Store(
                    id='store-1',
                    name='Test Store',
                    browser_backend='chrome',
                    created_at=datetime.now(UTC).isoformat(),
                )
                db.add(store)
            else:
                store = existing
            task = Task(
                id=task_id,
                title='Test task',
                status=status,
                plan_mode=plan_mode,
                plan=plan,
                platform=platform,
                country=country,
                schedule_id=schedule_id,
                store_id='store-1',
                created_by='test-user',
                created_at=datetime.now(UTC).isoformat(),
                updated_at=datetime.now(UTC).isoformat(),
            )
            db.add(task)
            await db.commit()
            return store, task

    return _create


class TestSubmitStatus:
    """submit() only sets QUEUED from PENDING/WAITING."""

    async def test_submit_pending_becomes_queued(
        self, db_session, store_and_task
    ):
        await store_and_task(status=TaskStatus.PENDING)
        scheduler = TaskQueueScheduler()

        with (
            patch('app.scheduler.task_queue.async_session', db_session),
            patch('app.scheduler.task_queue.event_bus', new_callable=AsyncMock),
        ):
            await scheduler.submit('task-1', 'store-1')

        async with db_session() as db:
            task = await db.get(Task, 'task-1')
            assert task.status == TaskStatus.QUEUED

    async def test_submit_planned_stays_planned(
        self, db_session, store_and_task
    ):
        await store_and_task(
            status=TaskStatus.PLANNED,
            plan_mode=True,
            plan='## Plan',
        )
        scheduler = TaskQueueScheduler()

        with (
            patch('app.scheduler.task_queue.async_session', db_session),
            patch('app.scheduler.task_queue.event_bus', new_callable=AsyncMock),
        ):
            await scheduler.submit('task-1', 'store-1')

        async with db_session() as db:
            task = await db.get(Task, 'task-1')
            assert task.status == TaskStatus.PLANNED

    async def test_submit_waiting_becomes_queued(
        self, db_session, store_and_task
    ):
        await store_and_task(status=TaskStatus.WAITING)
        scheduler = TaskQueueScheduler()

        with (
            patch('app.scheduler.task_queue.async_session', db_session),
            patch('app.scheduler.task_queue.event_bus', new_callable=AsyncMock),
        ):
            await scheduler.submit('task-1', 'store-1')

        async with db_session() as db:
            task = await db.get(Task, 'task-1')
            assert task.status == TaskStatus.QUEUED

    async def test_submit_enqueues_regardless_of_status(
        self, db_session, store_and_task
    ):
        """Task is added to the in-memory queue even when
        status is not overwritten."""
        await store_and_task(
            status=TaskStatus.PLANNED,
            plan_mode=True,
            plan='## Plan',
        )
        scheduler = TaskQueueScheduler()

        with (
            patch('app.scheduler.task_queue.async_session', db_session),
            patch('app.scheduler.task_queue.event_bus', new_callable=AsyncMock),
        ):
            await scheduler.submit('task-1', 'store-1')

        assert 'task-1' in scheduler._queues.get('store-1', [])


class TestRecovery:
    """_recover_from_db marks active tasks failed and re-queues
    pending/queued."""

    async def test_running_marked_failed(self, db_session, store_and_task):
        await store_and_task(status=TaskStatus.RUNNING)
        scheduler = TaskQueueScheduler()

        with patch('app.scheduler.task_queue.async_session', db_session):
            await scheduler._recover_from_db()

        async with db_session() as db:
            task = await db.get(Task, 'task-1')
            assert task.status == TaskStatus.FAILED
            assert task.error_category == 'server_restart'

    async def test_designing_marked_failed(self, db_session, store_and_task):
        await store_and_task(status=TaskStatus.DESIGNING)
        scheduler = TaskQueueScheduler()

        with patch('app.scheduler.task_queue.async_session', db_session):
            await scheduler._recover_from_db()

        async with db_session() as db:
            task = await db.get(Task, 'task-1')
            assert task.status == TaskStatus.FAILED

    async def test_queued_re_enqueued(self, db_session, store_and_task):
        await store_and_task(status=TaskStatus.QUEUED)
        scheduler = TaskQueueScheduler()

        with patch('app.scheduler.task_queue.async_session', db_session):
            await scheduler._recover_from_db()

        assert 'task-1' in scheduler._queues.get('store-1', [])

    async def test_pending_re_enqueued(self, db_session, store_and_task):
        await store_and_task(status=TaskStatus.PENDING)
        scheduler = TaskQueueScheduler()

        with patch('app.scheduler.task_queue.async_session', db_session):
            await scheduler._recover_from_db()

        assert 'task-1' in scheduler._queues.get('store-1', [])

    async def test_planned_scheduled_re_enqueued(
        self, db_session, store_and_task
    ):
        # A scheduled plan-mode task left PLANNED (frozen plan, awaiting a
        # slot) must resume across a restart — else it's orphaned forever.
        await store_and_task(
            status=TaskStatus.PLANNED,
            plan_mode=True,
            plan='## frozen plan',
            schedule_id='sched-1',
        )
        scheduler = TaskQueueScheduler()

        with patch('app.scheduler.task_queue.async_session', db_session):
            await scheduler._recover_from_db()

        assert 'task-1' in scheduler._queues.get('store-1', [])

    async def test_planned_interactive_not_re_enqueued(
        self, db_session, store_and_task
    ):
        # An interactive (non-scheduled) plan-mode task waits for the user
        # to press Run — it must NOT auto-resume on restart.
        await store_and_task(
            status=TaskStatus.PLANNED,
            plan_mode=True,
            plan='## user plan',
            schedule_id=None,
        )
        scheduler = TaskQueueScheduler()

        with patch('app.scheduler.task_queue.async_session', db_session):
            await scheduler._recover_from_db()

        assert 'task-1' not in scheduler._queues.get('store-1', [])
        async with db_session() as db:
            task = await db.get(Task, 'task-1')
            assert task.status == TaskStatus.PLANNED  # untouched


class TestCanSchedule:
    """can_schedule() scheduling decisions."""

    async def test_no_running_tasks_returns_run(
        self, db_session, store_and_task
    ):
        await store_and_task(platform='amazon', country='US')
        scheduler = TaskQueueScheduler()

        with patch('app.scheduler.task_queue.async_session', db_session):
            decision = await scheduler.can_schedule('task-1', 'store-1')
        assert decision == ScheduleDecision.RUN

    async def test_no_browser_session_returns_run_in_tab(
        self, db_session, store_and_task
    ):
        """When a task is running but no browser session exists
        (e.g. email-only task), new tasks should NOT be blocked.

        This was the bug: can_schedule fell through to QUEUE
        when no BrowserSession row existed."""
        await store_and_task(
            task_id='running-1',
            status=TaskStatus.RUNNING,
        )
        await store_and_task(
            task_id='new-1',
            platform='amazon',
            country='US',
        )
        scheduler = TaskQueueScheduler()
        scheduler._running_tasks['store-1'] = {'running-1'}

        with patch('app.scheduler.task_queue.async_session', db_session):
            decision = await scheduler.can_schedule('new-1', 'store-1')
        assert decision == ScheduleDecision.RUN_IN_NEW_TAB

    async def test_same_platform_same_country_concurrent(
        self, db_session, store_and_task
    ):
        """Same platform + same country → RUN_IN_NEW_TAB."""
        await store_and_task(
            task_id='running-1',
            status=TaskStatus.RUNNING,
            platform='amazon',
            country='US',
        )
        await store_and_task(
            task_id='new-1',
            platform='amazon',
            country='US',
        )
        # Create a browser session with matching platform/country
        async with db_session() as db:
            bs = BrowserSession(
                id='bs-1',
                store_id='store-1',
                status='running',
                current_platform='amazon',
                current_country='US',
            )
            db.add(bs)
            await db.commit()

        scheduler = TaskQueueScheduler()
        scheduler._running_tasks['store-1'] = {'running-1'}

        with patch('app.scheduler.task_queue.async_session', db_session):
            decision = await scheduler.can_schedule('new-1', 'store-1')
        assert decision == ScheduleDecision.RUN_IN_NEW_TAB

    async def test_same_platform_different_country_queued(
        self, db_session, store_and_task
    ):
        """Same platform + different country → QUEUE."""
        await store_and_task(
            task_id='running-1',
            status=TaskStatus.RUNNING,
            platform='amazon',
            country='US',
        )
        await store_and_task(
            task_id='new-1',
            platform='amazon',
            country='UK',
        )
        async with db_session() as db:
            bs = BrowserSession(
                id='bs-1',
                store_id='store-1',
                status='running',
                current_platform='amazon',
                current_country='US',
            )
            db.add(bs)
            await db.commit()

        scheduler = TaskQueueScheduler()
        scheduler._running_tasks['store-1'] = {'running-1'}

        with patch('app.scheduler.task_queue.async_session', db_session):
            decision = await scheduler.can_schedule('new-1', 'store-1')
        assert decision == ScheduleDecision.QUEUE
