"""The one-time move of forced-plan schedules to auto mode."""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.models.app_settings import AppSettings
from app.models.base import Base
from app.models.schedule import Schedule
from app.models.task import Task
from app.schedule_review_migration import (
    MARKER_KEY,
    migrate_forced_plan_schedules,
)

pytestmark = pytest.mark.unit


@pytest_asyncio.fixture
async def maker():
    engine = create_async_engine(
        'sqlite+aiosqlite://',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    await engine.dispose()


def _sched(**fields):
    return Schedule(
        id=str(uuid.uuid4()),
        title='t',
        schedule_type='days',
        schedule_time='09:00',
        created_by='u',
        timezone='UTC',
        **fields,
    )


async def test_forced_plan_schedules_become_auto_once(maker):
    async with maker() as db:
        ready = _sched(
            plan_mode=True, plan_status='ready', plan='P', is_active=False
        )
        planning = _sched(plan_mode=True, plan_status='planning')
        system = _sched(plan_mode=False, is_system=True, plan_status='none')
        db.add_all([ready, planning, system])
        await db.flush()
        planner = Task(
            title='plan',
            created_by='u',
            status='pending',
            plan_mode=True,
            is_plan_only=True,
            schedule_id=planning.id,
        )
        db.add(planner)
        planning.current_planning_task_id = planner.id
        await db.commit()
        ids = (ready.id, planning.id, system.id, planner.id)

    async with maker() as db:
        assert await migrate_forced_plan_schedules(db) == 2

    async with maker() as db:
        r, p, s, t = (
            await db.get(Schedule, ids[0]),
            await db.get(Schedule, ids[1]),
            await db.get(Schedule, ids[2]),
            await db.get(Task, ids[3]),
        )
        for row in (r, p):
            assert row.plan_mode is False
            assert row.plan_status == 'none'
            assert row.plan is None
            assert row.current_planning_task_id is None
        assert r.is_active is False, 'a paused schedule stays paused'
        assert s.is_system is True and s.plan_mode is False
        assert t.status == 'failed', 'its planner must not relaunch'
        assert await db.get(AppSettings, MARKER_KEY) is not None

        # A review schedule created after the migration is the owner's
        # choice: a second boot never touches it.
        chosen = _sched(plan_mode=True, plan_status='pending_approval')
        db.add(chosen)
        await db.commit()
        chosen_id = chosen.id

    async with maker() as db:
        assert await migrate_forced_plan_schedules(db) == 0
        assert (await db.get(Schedule, chosen_id)).plan_mode is True
