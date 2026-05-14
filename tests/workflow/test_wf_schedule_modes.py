"""Workflow tests for schedule phase_mode selection + editing.

Covers:
- Create-time ``phase_mode`` resolution (store-bound → single,
  client override, global default from AppSettings, fanout fallback).
- ``ScheduleUpdate`` rejects ``phase_mode`` / ``store_id`` via
  ``extra='forbid'``.
- Update preserves ``interval_value`` when it re-registers the
  APScheduler job (regression for a silent interval reset bug).
- Manual trigger routes by ``phase_mode`` (single → one no-store
  task, not fan-out).
- ``run_single_job`` creates exactly one ``store_id=None`` task.
"""

import asyncio
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.app_settings import AppSettings
from app.models.schedule import Schedule
from app.models.schedule_constants import PhaseMode
from app.models.store import Store
from app.models.task import Task
from app.scheduler.fanout import run_single_job

pytestmark = pytest.mark.workflow


@pytest_asyncio.fixture
async def sample_store(override_async_session):
    """A store row so store-bound schedule tests pass FK checks."""
    async with override_async_session() as db:
        store = Store(id=str(uuid.uuid4()), name='Test Store')
        db.add(store)
        await db.commit()
        await db.refresh(store)
        return store


def _valid_payload(**overrides):
    """Return a minimal POST /api/schedules payload."""
    body = {
        'title': 'Periodic check',
        'schedule_type': 'days',
        'schedule_time': '09:00',
        'interval_value': 1,
    }
    body.update(overrides)
    return body


class TestCreatePhaseMode:
    async def test_all_stores_defaults_to_fanout(self, admin_client):
        r = await admin_client.post('/api/schedules', json=_valid_payload())
        assert r.status_code == 201, r.text
        assert r.json()['phase_mode'] == PhaseMode.FANOUT.value

    async def test_store_bound_forces_single_even_if_fanout_requested(
        self,
        admin_client,
        sample_store,
    ):
        r = await admin_client.post(
            '/api/schedules',
            json=_valid_payload(
                store_id=sample_store.id,
                phase_mode='fanout',
            ),
        )
        assert r.status_code == 201, r.text
        assert r.json()['phase_mode'] == PhaseMode.SINGLE.value

    async def test_client_override_single_honored(self, admin_client):
        r = await admin_client.post(
            '/api/schedules',
            json=_valid_payload(phase_mode='single'),
        )
        assert r.status_code == 201, r.text
        assert r.json()['phase_mode'] == PhaseMode.SINGLE.value

    async def test_uses_global_default_when_unspecified(
        self,
        admin_client,
        override_async_session,
    ):
        async with override_async_session() as db:
            db.add(
                AppSettings(key='default_schedule_phase_mode', value='single')
            )
            await db.commit()

        r = await admin_client.post('/api/schedules', json=_valid_payload())
        assert r.status_code == 201, r.text
        assert r.json()['phase_mode'] == PhaseMode.SINGLE.value

    async def test_rejects_two_phase_from_client(self, admin_client):
        r = await admin_client.post(
            '/api/schedules',
            json=_valid_payload(phase_mode='two_phase'),
        )
        assert r.status_code == 400, r.text

    async def test_rejects_garbage_phase_mode(self, admin_client):
        r = await admin_client.post(
            '/api/schedules',
            json=_valid_payload(phase_mode='nonsense'),
        )
        assert r.status_code == 400, r.text


class TestUpdateImmutability:
    async def _make_schedule(self, admin_client):
        r = await admin_client.post(
            '/api/schedules',
            json=_valid_payload(interval_value=7),
        )
        assert r.status_code == 201
        return r.json()

    async def test_update_title_and_description(self, admin_client):
        created = await self._make_schedule(admin_client)
        r = await admin_client.put(
            f'/api/schedules/{created["id"]}',
            json={'title': 'Renamed', 'description': 'new desc'},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['title'] == 'Renamed'
        assert body['description'] == 'new desc'
        assert body['phase_mode'] == created['phase_mode']

    async def test_update_rejects_phase_mode_change(self, admin_client):
        created = await self._make_schedule(admin_client)
        r = await admin_client.put(
            f'/api/schedules/{created["id"]}',
            json={'phase_mode': 'single'},
        )
        assert r.status_code == 422, r.text

    async def test_update_rejects_store_id_change(self, admin_client):
        created = await self._make_schedule(admin_client)
        r = await admin_client.put(
            f'/api/schedules/{created["id"]}',
            json={'store_id': 'any-store-id'},
        )
        assert r.status_code == 422, r.text

    async def test_update_preserves_interval_value(
        self,
        admin_client,
        override_async_session,
    ):
        """Regression: PUT must keep ``interval_value=7`` after update.

        Before the fix, ``add_schedule_job`` was called without
        ``interval_value`` in the update path, so APScheduler silently
        fell back to 1.
        """
        created = await self._make_schedule(admin_client)
        r = await admin_client.put(
            f'/api/schedules/{created["id"]}',
            json={'title': 'Still every 7 days'},
        )
        assert r.status_code == 200, r.text

        # Confirm the persisted row still has interval_value=7
        async with override_async_session() as db:
            row = await db.get(Schedule, created['id'])
            assert row.interval_value == 7


class TestManualTrigger:
    """POST /api/schedules/{id}/trigger must honor phase_mode."""

    async def test_trigger_single_mode_creates_one_storeless_task(
        self,
        admin_client,
        override_async_session,
        install_fake_agent,
    ):
        r = await admin_client.post(
            '/api/schedules',
            json=_valid_payload(phase_mode='single'),
        )
        assert r.status_code == 201, r.text
        sched_id = r.json()['id']

        # User-created schedules are now plan_mode=True and start at
        # plan_status='planning'. Stop the backgrounded planning task
        # (so it doesn't race our update on the shared StaticPool
        # connection), then flip the schedule to 'ready' so /trigger
        # passes the fire-gate. Mirrors the UI after plan approval.
        await install_fake_agent.stop(sched_id)  # no-op if absent
        # Planning-task id is on the schedule row.
        async with override_async_session() as db:
            sched = await db.get(Schedule, sched_id)
            planning_task_id = sched.current_planning_task_id
        if planning_task_id:
            await install_fake_agent.stop(planning_task_id)
        async with override_async_session() as db:
            sched = await db.get(Schedule, sched_id)
            sched.plan_status = 'ready'
            sched.plan = 'canonical plan'
            sched.plan_version = 1
            sched.current_planning_task_id = None
            await db.commit()

        # Seed multiple stores — proves fanout was NOT invoked.
        async with override_async_session() as db:
            db.add(Store(id=str(uuid.uuid4()), name='Store A'))
            db.add(Store(id=str(uuid.uuid4()), name='Store B'))
            await db.commit()

        r = await admin_client.post(f'/api/schedules/{sched_id}/trigger')
        assert r.status_code == 200, r.text
        assert r.json().get('mode') == 'single'

        # Let the fire-and-forget asyncio.create_task drain. Exclude
        # the creation-time planning task (is_plan_only=True) from the
        # run-tasks query — we only want tasks created by the trigger.
        for _ in range(20):
            await asyncio.sleep(0.05)
            async with override_async_session() as db:
                result = await db.execute(
                    select(Task).where(
                        Task.schedule_id == sched_id,
                        Task.is_plan_only.is_(False),
                    )
                )
                tasks = result.scalars().all()
            if tasks:
                break

        assert len(tasks) == 1
        assert tasks[0].store_id is None


class TestSingleJobRunner:
    async def test_creates_one_storeless_task(
        self,
        override_async_session,
        admin_user,
        install_fake_agent,
    ):
        """``run_single_job`` creates exactly one Task with
        ``store_id=None`` linked to the schedule.
        """
        async with override_async_session() as db:
            sched = Schedule(
                id=str(uuid.uuid4()),
                title='IMAP sweep',
                schedule_type='minutes',
                schedule_time='00:00',
                interval_value=5,
                phase_mode=PhaseMode.SINGLE.value,
                created_by=admin_user.id,
            )
            db.add(sched)
            # Add a couple of stores — proving fanout is NOT invoked.
            db.add(Store(id=str(uuid.uuid4()), name='Store A'))
            db.add(Store(id=str(uuid.uuid4()), name='Store B'))
            await db.commit()
            sched_id = sched.id

        await run_single_job(
            schedule_id=sched_id,
            task_title='IMAP sweep',
            description='check shared inbox',
        )

        async with override_async_session() as db:
            result = await db.execute(
                select(Task).where(Task.schedule_id == sched_id)
            )
            tasks = result.scalars().all()
        assert len(tasks) == 1
        assert tasks[0].store_id is None
        assert tasks[0].title == 'IMAP sweep'
