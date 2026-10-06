"""A ``fanout_serial`` batch runs its stores one at a time.

The incident: a monthly fanout fired every store in the same instant.
Two of them drove the same portal through the one anti-detect client
and spent the run trading the 60 s launch lock back and forth — neither
got a browser. A cron time cannot stagger a fanout (it fires every store
at once), so the schedule says "one at a time" and the queue enforces it.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.schedule import Schedule
from app.models.store import Store
from app.models.task import Task
from app.scheduler.task_queue import ScheduleDecision, TaskQueueScheduler
from app.task_states import TaskStatus

pytestmark = pytest.mark.unit

T0 = datetime(2026, 1, 3, 2, 10, tzinfo=UTC)


@pytest.fixture
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


async def _batch(maker, *, serial, statuses):
    """One schedule, one batch, one child per store in creation order."""
    async with maker() as db:
        db.add(
            Schedule(
                id='sched-1',
                title='Monthly download',
                schedule_type='monthly',
                schedule_time='02:10',
                fanout_serial=serial,
                created_by='u',
            )
        )
        for i, status in enumerate(statuses):
            db.add(
                Store(id=f'store-{i}', name=f'S{i}', browser_backend='chrome')
            )
            db.add(
                Task(
                    id=f'task-{i}',
                    title='Monthly download',
                    status=status,
                    store_id=f'store-{i}',
                    schedule_id='sched-1',
                    batch_id='batch-1',
                    created_by='u',
                    created_at=(T0 + timedelta(milliseconds=i)).isoformat(),
                    updated_at=T0.isoformat(),
                )
            )
        await db.commit()


async def _decide(maker, i):
    sched = TaskQueueScheduler()
    with patch('app.scheduler.task_queue.async_session', maker):
        return await sched.can_schedule(f'task-{i}', f'store-{i}')


class TestSerialBatch:
    async def test_first_store_runs_the_rest_wait(self, maker):
        await _batch(maker, serial=True, statuses=[TaskStatus.QUEUED] * 3)
        assert await _decide(maker, 0) == ScheduleDecision.RUN
        assert await _decide(maker, 1) == ScheduleDecision.QUEUE
        assert await _decide(maker, 2) == ScheduleDecision.QUEUE

    async def test_next_store_is_released_when_the_one_ahead_ends(self, maker):
        await _batch(
            maker,
            serial=True,
            statuses=[TaskStatus.FAILED, TaskStatus.QUEUED, TaskStatus.QUEUED],
        )
        # A failed store must not hold the line: the batch still owes
        # the others their run.
        assert await _decide(maker, 1) == ScheduleDecision.RUN
        assert await _decide(maker, 2) == ScheduleDecision.QUEUE

    @pytest.mark.parametrize(
        'ahead',
        [
            TaskStatus.PENDING,
            TaskStatus.PLANNED,
            TaskStatus.DESIGNING,
            TaskStatus.RUNNING,
        ],
    )
    async def test_every_active_state_holds_the_line(self, maker, ahead):
        # A fanout with a frozen plan creates its children PLANNED, so
        # PLANNED has to count as "still ahead" or the gate never closes.
        await _batch(maker, serial=True, statuses=[ahead, TaskStatus.PLANNED])
        assert await _decide(maker, 1) == ScheduleDecision.QUEUE

    async def test_a_store_waiting_on_the_user_does_not_hold_the_line(
        self, maker
    ):
        # WAITING can last until the next fire cancels it — a month on a
        # monthly schedule — and holds no agent meanwhile.
        await _batch(
            maker, serial=True, statuses=[TaskStatus.WAITING, TaskStatus.QUEUED]
        )
        assert await _decide(maker, 1) == ScheduleDecision.RUN

    async def test_default_fanout_still_runs_every_store_at_once(self, maker):
        await _batch(maker, serial=False, statuses=[TaskStatus.QUEUED] * 3)
        for i in range(3):
            assert await _decide(maker, i) == ScheduleDecision.RUN
