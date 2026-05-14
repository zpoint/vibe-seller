"""Workflow tests for the fire-gate on plan-mode schedules.

Scheduler jobs (cron + fanout) refuse to fire when a plan-mode
schedule is not ``plan_status=READY``. Manual ``/trigger`` and
``/resume`` endpoints mirror the gate with 409 responses.  System
(``is_system=True``) and ``plan_mode=False`` schedules bypass the
gate.
"""

import uuid

import pytest
from sqlalchemy import select

import app.database as _db
from app.models.schedule import Schedule
from app.models.task import Task
from app.models.user import User
from app.scheduler.fanout import run_fanout_job, run_single_job
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow


async def _mk_schedule(client, *, plan_mode, plan_status, description='desc'):
    """Create a Schedule row directly in DB.

    We bypass POST /api/schedules here because its plan-mode flow
    spawns a background planning task — which races with the test's
    attempt to force a specific plan_status for test isolation.
    """
    sched_id = str(uuid.uuid4())
    async with _db.async_session() as db:
        # Find the admin user id (conftest created one).
        u_result = await db.execute(select(User).limit(1))
        user = u_result.scalars().first()
        assert user is not None
        s = Schedule(
            id=sched_id,
            title=f'sched-{plan_status}',
            description=description,
            schedule_type='days',
            schedule_time='09:05',
            plan_mode=plan_mode,
            plan_status=plan_status,
            plan_version=1 if plan_status == 'ready' else 0,
            plan=('canonical plan text' if plan_status == 'ready' else None),
            created_by=user.id,
            phase_mode='fanout',
            timezone='UTC',
        )
        db.add(s)
        await db.commit()
    return sched_id


class TestTriggerGate:
    async def test_trigger_rejects_stale(
        self, admin_client, install_fake_agent
    ):
        sched_id = await _mk_schedule(
            admin_client, plan_mode=True, plan_status='stale'
        )
        r = await admin_client.post(f'/api/schedules/{sched_id}/trigger')
        assert r.status_code == 409

    async def test_trigger_rejects_none(self, admin_client, install_fake_agent):
        sched_id = await _mk_schedule(
            admin_client, plan_mode=True, plan_status='none'
        )
        r = await admin_client.post(f'/api/schedules/{sched_id}/trigger')
        assert r.status_code == 409

    async def test_trigger_rejects_failed(
        self, admin_client, install_fake_agent
    ):
        sched_id = await _mk_schedule(
            admin_client, plan_mode=True, plan_status='failed'
        )
        r = await admin_client.post(f'/api/schedules/{sched_id}/trigger')
        assert r.status_code == 409

    async def test_trigger_allows_non_plan_mode(
        self, admin_client, install_fake_agent
    ):
        """plan_mode=False schedules fire regardless of plan_status."""
        sched_id = await _mk_schedule(
            admin_client, plan_mode=False, plan_status='none'
        )
        r = await admin_client.post(f'/api/schedules/{sched_id}/trigger')
        assert r.status_code == 200


class TestResumeGate:
    async def test_resume_rejects_stale(self, admin_client, install_fake_agent):
        sched_id = await _mk_schedule(
            admin_client, plan_mode=True, plan_status='stale'
        )
        r = await admin_client.post(f'/api/schedules/{sched_id}/resume')
        assert r.status_code == 409

    async def test_resume_allows_ready(self, admin_client, install_fake_agent):
        sched_id = await _mk_schedule(
            admin_client, plan_mode=True, plan_status='ready'
        )
        r = await admin_client.post(f'/api/schedules/{sched_id}/resume')
        assert r.status_code == 200, r.text

    async def test_resume_allows_non_plan_mode(
        self, admin_client, install_fake_agent
    ):
        sched_id = await _mk_schedule(
            admin_client, plan_mode=False, plan_status='none'
        )
        r = await admin_client.post(f'/api/schedules/{sched_id}/resume')
        assert r.status_code == 200


class TestFanoutGate:
    async def test_fanout_skips_stale_without_creating_tasks(
        self, admin_client, install_fake_agent
    ):
        """run_fanout_job with plan_status='stale' creates zero tasks."""
        sched_id = await _mk_schedule(
            admin_client, plan_mode=True, plan_status='stale'
        )
        # Seed at least one store so a ready-state fanout would work.
        await admin_client.post('/api/stores', json={'name': 'S1'})

        await run_fanout_job(
            schedule_id=sched_id,
            task_title='t',
            description='d',
            plan_mode=True,
        )
        async with _db.async_session() as db:
            rows = await db.execute(
                select(Task).where(Task.schedule_id == sched_id)
            )
            assert rows.scalars().all() == []

    async def test_fanout_ready_creates_tasks_with_plan(
        self, admin_client, install_fake_agent
    ):
        """run_fanout_job with plan_status='ready' creates per-store tasks
        pre-loaded with the schedule plan + plan_version."""
        sched_id = await _mk_schedule(
            admin_client, plan_mode=True, plan_status='ready'
        )
        # Install fake agent so the queued tasks don't try to run a
        # real agent.
        install_fake_agent.default_scenario = FakeAgentScenario()
        await admin_client.post('/api/stores', json={'name': 'Shop A'})
        await admin_client.post('/api/stores', json={'name': 'Shop B'})

        await run_fanout_job(
            schedule_id=sched_id,
            task_title='Check',
            description='d',
            plan_mode=True,
        )
        async with _db.async_session() as db:
            rows = await db.execute(
                select(Task).where(Task.schedule_id == sched_id)
            )
            tasks = rows.scalars().all()
            # One task per store seeded above.
            assert len(tasks) >= 2
            for t in tasks:
                assert t.plan == 'canonical plan text'
                assert t.plan_version == 1

    async def test_single_job_skips_stale(
        self, admin_client, install_fake_agent
    ):
        sched_id = await _mk_schedule(
            admin_client, plan_mode=True, plan_status='stale'
        )
        await run_single_job(
            schedule_id=sched_id,
            task_title='t',
            description='d',
            plan_mode=True,
        )
        async with _db.async_session() as db:
            rows = await db.execute(
                select(Task).where(Task.schedule_id == sched_id)
            )
            assert rows.scalars().all() == []
