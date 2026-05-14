"""Workflow tests for Schedule plan invalidation + /replan.

Covers:
- Prompt edit invalidates (plan_status -> stale), old plan retained.
- Whitespace-only description edits do NOT invalidate.
- Cron/timezone edits do NOT invalidate.
- plan_mode is immutable (400).
- Optimistic lock via plan_version (412 on mismatch).
- /replan idempotent.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import select

import app.database as _db
from app.events.bus import event_bus
from app.models.schedule import Schedule
from app.models.user import User
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow


async def _make_ready_schedule(admin_client, install_fake_agent):
    """Create a plan-mode schedule, wait for auto-approved plan."""
    install_fake_agent.default_scenario = FakeAgentScenario(
        plan='## Plan v1\n1. Step one',
    )
    r = await admin_client.post(
        '/api/schedules',
        json={
            'title': 'Check links',
            'description': 'Check product links for cart anomalies',
            'schedule_type': 'days',
            'schedule_time': '09:05',
            'plan_mode': True,
        },
    )
    assert r.status_code == 201
    sched = r.json()
    # Plan-only auto-approves at ExitPlanMode — no /execute-plan call.
    for _ in range(60):
        r = await admin_client.get(f'/api/schedules/{sched["id"]}')
        if r.json()['plan_status'] == 'ready':
            return r.json()
        await asyncio.sleep(0.05)
    raise AssertionError('Schedule never reached ready state')


class TestInvalidation:
    async def test_prompt_edit_invalidates(
        self, admin_client, install_fake_agent
    ):
        sched = await _make_ready_schedule(admin_client, install_fake_agent)
        # Subscribe BEFORE the PUT to catch the stale SSE — without
        # this, the frontend badge + SchedulePlanPanel stay stale
        # until the next full page load.
        q = event_bus.subscribe()
        try:
            r = await admin_client.put(
                f'/api/schedules/{sched["id"]}',
                json={
                    'description': 'Different instruction entirely',
                    'plan_version': sched['plan_version'],
                },
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body['plan_status'] == 'stale'

            # Drain the subscriber queue and look for our event.
            # Queue is bounded-blocking; poll with nowait.
            events = []
            while not q.empty():
                events.append(q.get_nowait())
            assert any(
                '"type": "schedule_plan_stale"' in evt and sched['id'] in evt
                for evt in events
            ), (
                'schedule_plan_stale SSE must fire so the frontend '
                f'can refresh. Got events: {events}'
            )
        finally:
            event_bus.unsubscribe(q)
        # Old plan text retained for diff display.
        assert body['plan'] == sched['plan']

    async def test_whitespace_only_prompt_edit_does_not_invalidate(
        self, admin_client, install_fake_agent
    ):
        sched = await _make_ready_schedule(admin_client, install_fake_agent)
        # Add leading/trailing whitespace only.
        r = await admin_client.put(
            f'/api/schedules/{sched["id"]}',
            json={
                'description': '   Check product links for cart anomalies\n',
                'plan_version': sched['plan_version'],
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()['plan_status'] == 'ready'

    async def test_cron_edit_does_not_invalidate(
        self, admin_client, install_fake_agent
    ):
        sched = await _make_ready_schedule(admin_client, install_fake_agent)
        r = await admin_client.put(
            f'/api/schedules/{sched["id"]}',
            json={
                'schedule_time': '10:00',
                'plan_version': sched['plan_version'],
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()['plan_status'] == 'ready'

    async def test_timezone_edit_does_not_invalidate(
        self, admin_client, install_fake_agent
    ):
        sched = await _make_ready_schedule(admin_client, install_fake_agent)
        r = await admin_client.put(
            f'/api/schedules/{sched["id"]}',
            json={
                'timezone': 'Asia/Tokyo',
                'plan_version': sched['plan_version'],
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()['plan_status'] == 'ready'


class TestImmutablePlanMode:
    async def test_plan_mode_change_rejected(
        self, admin_client, install_fake_agent
    ):
        sched = await _make_ready_schedule(admin_client, install_fake_agent)
        r = await admin_client.put(
            f'/api/schedules/{sched["id"]}',
            json={
                'plan_mode': False,
                'plan_version': sched['plan_version'],
            },
        )
        assert r.status_code == 400, r.text

    async def test_plan_mode_same_value_ok(
        self, admin_client, install_fake_agent
    ):
        sched = await _make_ready_schedule(admin_client, install_fake_agent)
        r = await admin_client.put(
            f'/api/schedules/{sched["id"]}',
            json={
                'plan_mode': True,  # unchanged
                'plan_version': sched['plan_version'],
            },
        )
        assert r.status_code == 200


class TestOptimisticLock:
    async def test_stale_plan_version_rejected(
        self, admin_client, install_fake_agent
    ):
        sched = await _make_ready_schedule(admin_client, install_fake_agent)
        r = await admin_client.put(
            f'/api/schedules/{sched["id"]}',
            json={
                'description': 'New prompt',
                'plan_version': sched['plan_version'] - 1,
            },
        )
        assert r.status_code == 412, r.text

    async def test_missing_plan_version_ok(
        self, admin_client, install_fake_agent
    ):
        """Omitting plan_version is allowed (no lock) for back-compat clients."""
        sched = await _make_ready_schedule(admin_client, install_fake_agent)
        r = await admin_client.put(
            f'/api/schedules/{sched["id"]}',
            json={'title': 'Renamed'},
        )
        assert r.status_code == 200


class TestReplan:
    async def test_replan_spawns_new_task_from_stale(
        self, admin_client, install_fake_agent
    ):
        sched = await _make_ready_schedule(admin_client, install_fake_agent)
        # Invalidate.
        await admin_client.put(
            f'/api/schedules/{sched["id"]}',
            json={
                'description': 'Different instruction',
                'plan_version': sched['plan_version'],
            },
        )
        r = await admin_client.post(f'/api/schedules/{sched["id"]}/replan')
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['plan_status'] == 'planning'
        assert body['current_planning_task_id']

    async def test_replan_idempotent_when_planning(
        self, admin_client, install_fake_agent
    ):
        """Two replan calls in a row return same planning task."""
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan='## plan', design_delay=1.0
        )
        r = await admin_client.post(
            '/api/schedules',
            json={
                'title': 'X',
                'description': 'y',
                'schedule_type': 'days',
                'schedule_time': '09:05',
                'plan_mode': True,
            },
        )
        sched_id = r.json()['id']
        first_task_id = r.json()['current_planning_task_id']

        # Second /replan while the first planner is still running →
        # should return the same task id without spawning a duplicate.
        r2 = await admin_client.post(f'/api/schedules/{sched_id}/replan')
        assert r2.status_code == 200
        assert r2.json()['current_planning_task_id'] == first_task_id

    async def test_replan_rejected_for_system_schedule(
        self, admin_client, install_fake_agent
    ):
        """System schedules are seeded with plan_mode=False + is_system=True
        and bypass the planning lifecycle entirely. /replan must reject.

        Note: API cannot create a non-plan-mode schedule anymore
        (server forces plan_mode=True for user-created schedules),
        so this scenario can only happen for system-seeded rows. We
        insert one directly in the DB.
        """
        sched_id = str(uuid.uuid4())
        async with _db.async_session() as db:
            u = (await db.execute(select(User).limit(1))).scalars().first()
            db.add(
                Schedule(
                    id=sched_id,
                    title='sys',
                    schedule_type='days',
                    schedule_time='09:05',
                    plan_mode=False,
                    is_system=True,
                    plan_status='none',
                    created_by=u.id,
                    phase_mode='fanout',
                    timezone='UTC',
                )
            )
            await db.commit()

        rr = await admin_client.post(f'/api/schedules/{sched_id}/replan')
        assert rr.status_code == 400
