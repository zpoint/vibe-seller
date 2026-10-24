"""A store deleted while a fanout's L2 phase runs gets no task.

The fanout reads its store list before the L2 phase, which can run for
minutes. In CI another test deleted its store in that window; the fanout
still created that store's task, which then failed at dispatch with
"Store not found" and failed the batch.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, select
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
from app.scheduler import fanout

pytestmark = pytest.mark.unit


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


async def test_store_deleted_during_l2_gets_no_task(maker):
    async with maker() as db:
        db.add(
            Schedule(
                id='sched-1',
                title='Catalog sync',
                schedule_type='daily',
                schedule_time='02:10',
                phase_mode='two_phase',
                is_system=True,
                created_by='u',
            )
        )
        for sid in ('store-keep', 'store-gone'):
            db.add(Store(id=sid, name=sid, browser_backend='chrome'))
        await db.commit()

    async def l2_phase(**_kw):
        # Another client deletes a store while the L2 task runs.
        async with maker() as db:
            await db.execute(delete(Store).where(Store.id == 'store-gone'))
            await db.commit()

    submit = AsyncMock()
    with (
        patch.object(fanout, 'async_session', maker),
        patch.object(fanout, '_run_l2_phase', l2_phase),
        patch.object(fanout.task_queue_scheduler, 'submit', submit),
        patch.object(
            fanout, 'resolve_schedule_profile', AsyncMock(return_value='p')
        ),
        patch.object(fanout.telemetry, 'send'),
        patch.object(fanout.event_bus, 'emit', AsyncMock()),
    ):
        await fanout.run_fanout_job('sched-1', 'Catalog sync')

    async with maker() as db:
        stores = (await db.execute(select(Task.store_id))).scalars().all()
    assert stores == ['store-keep']
    assert [c.args[1] for c in submit.await_args_list] == ['store-keep']
