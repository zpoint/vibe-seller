"""Workflow tests for next-fire cancel-forward of stuck WAITING tasks.

Rationale: silently skipping a store's fire because the prior run is
WAITING would stall a daily schedule for up to 30 days. Instead, on
the next fire we cancel the prior WAITING task (FAILED with
``error_category='superseded_by_next_fire'``) and create a fresh run
for that store.
"""

import uuid

import pytest
from sqlalchemy import select

import app.database as _db
from app.models.schedule import Schedule
from app.models.task import Task
from app.models.user import User
from app.scheduler.fanout import run_fanout_job
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow


async def _mk_ready_schedule(description='d'):
    """Direct-DB helper: plan-ready schedule."""
    sid = str(uuid.uuid4())
    async with _db.async_session() as db:
        u = (await db.execute(select(User).limit(1))).scalars().first()
        s = Schedule(
            id=sid,
            title='sched',
            description=description,
            schedule_type='days',
            schedule_time='09:05',
            plan_mode=True,
            plan_status='ready',
            plan_version=1,
            plan='P',
            created_by=u.id,
            phase_mode='fanout',
            timezone='UTC',
        )
        db.add(s)
        await db.commit()
    return sid


class TestCancelForward:
    async def test_fanout_cancels_prior_waiting_for_same_store(
        self, admin_client, install_fake_agent
    ):
        install_fake_agent.default_scenario = FakeAgentScenario()
        sr = await admin_client.post('/api/stores', json={'name': 'Cart Shop'})
        store_id = sr.json()['id']

        sched_id = await _mk_ready_schedule()

        # Seed a prior WAITING task for this (schedule, store).
        stale_task_id = str(uuid.uuid4())
        async with _db.async_session() as db:
            t = Task(
                id=stale_task_id,
                store_id=store_id,
                schedule_id=sched_id,
                created_by='admin',
                title='Previous run',
                status='waiting',
                plan_mode=True,
                wait_condition='{"reason":"awaiting user"}',
            )
            db.add(t)
            await db.commit()

        await run_fanout_job(
            schedule_id=sched_id,
            task_title='Daily',
            description='do the thing',
            plan_mode=True,
        )

        async with _db.async_session() as db:
            stale = await db.get(Task, stale_task_id)
            assert stale.status == 'failed'
            assert stale.error_category == 'superseded_by_next_fire'

            # A fresh Task exists for the same store (status=planned
            # because schedule has a plan).
            rows = await db.execute(
                select(Task).where(
                    Task.schedule_id == sched_id,
                    Task.store_id == store_id,
                    Task.id != stale_task_id,
                )
            )
            fresh = rows.scalars().all()
            assert len(fresh) == 1
            assert fresh[0].status == 'planned'
            assert fresh[0].plan == 'P'

    async def test_other_store_unaffected(
        self, admin_client, install_fake_agent
    ):
        """A WAITING task for store A does not block store B's fire."""
        install_fake_agent.default_scenario = FakeAgentScenario()
        ra = await admin_client.post('/api/stores', json={'name': 'A'})
        rb = await admin_client.post('/api/stores', json={'name': 'B'})
        a_id, b_id = ra.json()['id'], rb.json()['id']

        sched_id = await _mk_ready_schedule()

        # Prior WAITING only for store A.
        async with _db.async_session() as db:
            t = Task(
                id=str(uuid.uuid4()),
                store_id=a_id,
                schedule_id=sched_id,
                created_by='admin',
                title='prev',
                status='waiting',
                plan_mode=True,
            )
            db.add(t)
            await db.commit()

        await run_fanout_job(
            schedule_id=sched_id,
            task_title='T',
            description='d',
            plan_mode=True,
        )

        async with _db.async_session() as db:
            rows = await db.execute(
                select(Task).where(
                    Task.schedule_id == sched_id,
                    Task.store_id == b_id,
                )
            )
            b_tasks = rows.scalars().all()
            assert len(b_tasks) == 1  # B got a fresh run, no prior task
            assert b_tasks[0].status == 'planned'
