"""Unit tests for the stuck-planning reaper."""

from datetime import UTC, datetime, timedelta
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

import app.ai.claude_backend_manager as _cb
import app.database as _db
from app.models.base import Base
from app.models.schedule import Schedule
from app.models.task import Task
import app.scheduler.plan_reaper as _reaper

pytestmark = pytest.mark.unit


class _FakeManager:
    """Substitute for agent_manager — records stop() calls."""

    def __init__(self):
        self.stopped: list[str] = []

    async def stop(self, task_id: str) -> bool:
        self.stopped.append(task_id)
        return True


@pytest_asyncio.fixture
async def reaper_env(monkeypatch):
    """In-memory DB + fake agent_manager for reaper tests."""
    engine = create_async_engine(
        'sqlite+aiosqlite://',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr(_db, 'async_session', maker)
    monkeypatch.setattr(_reaper, 'async_session', maker)
    fake = _FakeManager()
    monkeypatch.setattr(_reaper, 'agent_manager', fake)
    monkeypatch.setattr(_cb, 'agent_manager', fake)
    yield maker, fake


async def _mk(maker, *, plan_status, task_status, minutes_ago):
    """Seed one Schedule + pointed-to Task."""
    sched_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    stale_ts = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()
    async with maker() as db:
        t = Task(
            id=task_id,
            created_by='u',
            title='plan',
            status=task_status,
            plan_mode=True,
            is_plan_only=True,
            updated_at=stale_ts,
            schedule_id=sched_id,
        )
        s = Schedule(
            id=sched_id,
            title='t',
            schedule_type='days',
            schedule_time='09:05',
            plan_mode=True,
            plan_status=plan_status,
            current_planning_task_id=task_id,
            created_by='u',
            phase_mode='fanout',
            timezone='UTC',
        )
        db.add(s)
        db.add(t)
        await db.commit()
    return sched_id, task_id


class TestReaper:
    async def test_stuck_designing_task_fails(self, reaper_env):
        maker, fake = reaper_env
        sched_id, task_id = await _mk(
            maker,
            plan_status='planning',
            task_status='designing',
            minutes_ago=60,
        )
        await _reaper.reap_stuck_planning_tasks()
        async with maker() as db:
            s = await db.get(Schedule, sched_id)
            t = await db.get(Task, task_id)
            assert s.plan_status == 'failed'
            assert s.plan_error == 'Planning timed out'
            assert s.current_planning_task_id is None
            assert t.status == 'failed'
            assert t.error_category == 'planning_timeout'
        # Agent was asked to stop.
        assert task_id in fake.stopped

    async def test_waiting_task_not_reaped(self, reaper_env):
        """WAITING is legitimate — user is being asked a question."""
        maker, fake = reaper_env
        sched_id, task_id = await _mk(
            maker,
            plan_status='planning',
            task_status='waiting',
            minutes_ago=60,
        )
        await _reaper.reap_stuck_planning_tasks()
        async with maker() as db:
            s = await db.get(Schedule, sched_id)
            t = await db.get(Task, task_id)
            assert s.plan_status == 'planning'
            assert t.status == 'waiting'
        assert task_id not in fake.stopped

    async def test_fresh_task_not_reaped(self, reaper_env):
        maker, _ = reaper_env
        sched_id, task_id = await _mk(
            maker,
            plan_status='planning',
            task_status='designing',
            minutes_ago=5,  # younger than threshold
        )
        await _reaper.reap_stuck_planning_tasks()
        async with maker() as db:
            s = await db.get(Schedule, sched_id)
            t = await db.get(Task, task_id)
            assert s.plan_status == 'planning'
            assert t.status == 'designing'

    async def test_completed_task_syncs_schedule(self, reaper_env):
        """Defensive: if hook failed to flip, reaper should."""
        maker, _ = reaper_env
        sched_id, task_id = await _mk(
            maker,
            plan_status='planning',
            task_status='completed',
            minutes_ago=1,
        )
        # Give the schedule a plan so reaper can mark it READY.
        async with maker() as db:
            s = await db.get(Schedule, sched_id)
            s.plan = 'P'
            await db.commit()

        await _reaper.reap_stuck_planning_tasks()
        async with maker() as db:
            s = await db.get(Schedule, sched_id)
            assert s.plan_status == 'ready'
            assert s.current_planning_task_id is None

    async def test_completed_task_with_empty_plan_flips_failed(
        self, reaper_env
    ):
        """Regression guard: if the agent skipped ExitPlanMode, the
        planning task completes but Schedule.plan stays empty. The
        reaper must flip the schedule to FAILED with a clear error
        instead of leaving it stuck at plan_status='planning'."""
        maker, _ = reaper_env
        sched_id, task_id = await _mk(
            maker,
            plan_status='planning',
            task_status='completed',
            minutes_ago=1,
        )
        # Schedule.plan left None (the production failure mode).
        await _reaper.reap_stuck_planning_tasks()
        async with maker() as db:
            s = await db.get(Schedule, sched_id)
            assert s.plan_status == 'failed'
            assert s.current_planning_task_id is None
            assert s.plan_error and 'ExitPlanMode' in s.plan_error

    async def test_missing_pointer_cleaned_up(self, reaper_env):
        """Schedule in planning with no pointer → failed (repair)."""
        maker, _ = reaper_env
        sched_id = str(uuid.uuid4())
        async with maker() as db:
            s = Schedule(
                id=sched_id,
                title='t',
                schedule_type='days',
                schedule_time='09:05',
                plan_mode=True,
                plan_status='planning',
                current_planning_task_id=None,
                created_by='u',
                phase_mode='fanout',
                timezone='UTC',
            )
            db.add(s)
            await db.commit()

        await _reaper.reap_stuck_planning_tasks()
        async with maker() as db:
            s = await db.get(Schedule, sched_id)
            assert s.plan_status == 'failed'
