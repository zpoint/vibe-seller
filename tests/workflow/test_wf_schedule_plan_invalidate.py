"""Workflow tests for review-schedule edits, the review switch and /replan.

Covers:
- A prompt edit drops the approved plan and starts a new planner at once.
- Whitespace-only description edits do NOT invalidate.
- Cron/timezone edits do NOT invalidate.
- plan_mode (review) switches on and off; the in-task agent cannot
  switch it off.
- Optimistic lock via plan_version (412 on mismatch) — only for edits
  that change the plan.
- /replan idempotent.
"""

import uuid

import pytest
from sqlalchemy import select

import app.database as _db
from app.events.bus import event_bus
from app.models.schedule import Schedule
from app.models.user import User
from tests.workflow.fake_agent import FakeAgentScenario
from tests.workflow.schedule_review_helpers import approve_when_proposed

pytestmark = pytest.mark.workflow


async def _make_ready_schedule(admin_client, install_fake_agent):
    """Create a review schedule and approve its plan (as the UI does)."""
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
    return await approve_when_proposed(admin_client, sched['id'])


class TestInvalidation:
    async def test_prompt_edit_drops_plan_and_replans(
        self, admin_client, install_fake_agent
    ):
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan='## Plan v2', design_delay=0.5
        )
        sched = await _make_ready_schedule(admin_client, install_fake_agent)
        # Subscribe BEFORE the PUT to catch the SSE — without it, the
        # frontend badge + SchedulePlanPanel stay stale until the next
        # full page load.
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
            assert body['plan_status'] == 'planning'
            assert body['current_planning_task_id']

            events = []
            while not q.empty():
                events.append(q.get_nowait())
            assert any(
                '"type": "schedule_plan_changed"' in evt and sched['id'] in evt
                for evt in events
            ), (
                'schedule_plan_changed SSE must fire so the frontend '
                f'can refresh. Got events: {events}'
            )
        finally:
            event_bus.unsubscribe(q)
        # The approved plan answered the old prompt: it must not fire
        # for the new one.
        assert body['plan'] is None

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


class TestReviewSwitch:
    async def test_review_off_drops_the_plan(
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
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['plan_mode'] is False
        assert body['plan_status'] == 'none'
        assert body['plan'] is None
        assert body['current_planning_task_id'] is None

    async def test_review_on_starts_a_planner(
        self, admin_client, install_fake_agent
    ):
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan='## plan', design_delay=0.5
        )
        r = await admin_client.post(
            '/api/schedules',
            json={
                'title': 'Auto first',
                'description': 'd',
                'schedule_type': 'days',
                'schedule_time': '09:05',
            },
        )
        sched = r.json()
        assert sched['plan_status'] == 'none'
        r = await admin_client.put(
            f'/api/schedules/{sched["id"]}', json={'plan_mode': True}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['plan_mode'] is True
        assert body['plan_status'] == 'planning'
        assert body['current_planning_task_id']

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
        assert r.json()['plan_status'] == 'ready'

    async def test_task_agent_cannot_switch_review_off(
        self, admin_client, ai_bot_client, install_fake_agent
    ):
        """The in-task agent's credential resolves to ai_bot: it must not
        walk around the review it runs under."""
        sched = await _make_ready_schedule(admin_client, install_fake_agent)
        r = await ai_bot_client.put(
            f'/api/schedules/{sched["id"]}', json={'plan_mode': False}
        )
        assert r.status_code == 403, r.text
        after = (await admin_client.get(f'/api/schedules/{sched["id"]}')).json()
        assert after['plan_mode'] is True
        assert after['plan_status'] == 'ready'


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

    async def test_stale_version_does_not_block_a_title_edit(
        self, admin_client, install_fake_agent
    ):
        """The lock guards the plan, not the title: an edit form opened
        before a proposal landed must still save a rename."""
        sched = await _make_ready_schedule(admin_client, install_fake_agent)
        r = await admin_client.put(
            f'/api/schedules/{sched["id"]}',
            json={'title': 'Renamed', 'plan_version': 0},
        )
        assert r.status_code == 200, r.text

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
    async def test_replan_spawns_new_task_from_failed(
        self, admin_client, install_fake_agent
    ):
        sched = await _make_ready_schedule(admin_client, install_fake_agent)
        async with _db.async_session() as db:
            row = await db.get(Schedule, sched['id'])
            row.plan_status = 'failed'
            row.plan_error = 'The planning agent stopped.'
            await db.commit()
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

        System rows are seeded, never created through the API, so we
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
