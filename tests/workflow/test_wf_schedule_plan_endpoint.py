"""Workflow tests for GET /api/schedules/{id}/plan and the runs-list
filter that excludes plan-only Tasks.

These are the surfaces the new SchedulePlanPanel depends on. The
runs-list test is also a regression guard for the #136 bug: the
planning task used to appear in the "System" bucket of
AllStoresTaskList.
"""

import asyncio

import pytest
from sqlalchemy import select

import app.database as _db
from app.models.schedule import Schedule
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow


async def _wait_schedule_status(client, schedule_id, target, timeout=5.0):
    """Poll DB direct — API-mediated polling can race with FakeAgent's
    background task under StaticPool. Matches the pattern used by
    tests/workflow/test_wf_schedule_plan_user_pref.py."""
    for _ in range(int(timeout / 0.05)):
        async with _db.async_session() as db:
            s = (
                await db.execute(
                    select(Schedule).where(Schedule.id == schedule_id)
                )
            ).scalar_one_or_none()
            if s is not None and s.plan_status == target:
                # Hand back a dict shape matching the API response
                # used by downstream assertions.
                return {
                    'plan_status': s.plan_status,
                    'plan_version': s.plan_version,
                    'plan': s.plan,
                    'current_planning_task_id': s.current_planning_task_id,
                }
        await asyncio.sleep(0.05)
    raise AssertionError(
        f'Schedule {schedule_id} plan_status did not reach {target}'
    )


async def _create_plan_schedule(client, **extras):
    """Create a plan-mode schedule (minimal body — mirrors real UI)."""
    body = {
        'title': 'T',
        'description': 'do thing',
        'schedule_type': 'days',
        'schedule_time': '09:00',
    }
    body.update(extras)
    r = await client.post('/api/schedules', json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def _wait_planning_task_id(client, schedule_id, timeout=5.0):
    for _ in range(int(timeout / 0.05)):
        s = (await client.get(f'/api/schedules/{schedule_id}')).json()
        tid = s.get('current_planning_task_id')
        if tid:
            return tid
        await asyncio.sleep(0.05)
    raise AssertionError('No planning task spawned')


class TestTasksEndpointExcludesPlanOnly:
    async def test_planning_task_not_in_runs_list(
        self, admin_client, install_fake_agent
    ):
        """GET /{id}/tasks must NOT return the is_plan_only row.

        Before the fix, the planning task appeared in the runs list
        (inflating the count and landing in the "System" bucket).
        """
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan='## plan', design_delay=0.2
        )
        sched = await _create_plan_schedule(admin_client)
        # Wait until the planning task is registered on the schedule.
        planning_id = await _wait_planning_task_id(admin_client, sched['id'])

        r = await admin_client.get(f'/api/schedules/{sched["id"]}/tasks')
        assert r.status_code == 200
        tasks = r.json()
        assert all(t['id'] != planning_id for t in tasks), (
            'plan-only task leaked into runs list'
        )


class TestPlanEndpoint:
    async def test_planning_state(self, admin_client, install_fake_agent):
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan='## pending plan', design_delay=0.5
        )
        sched = await _create_plan_schedule(admin_client)
        planning_id = await _wait_planning_task_id(admin_client, sched['id'])

        r = await admin_client.get(f'/api/schedules/{sched["id"]}/plan')
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['plan_status'] == 'planning'
        assert body['current_planning_task_id'] == planning_id
        assert body['plan_text'] in (None, '', 'pending')
        # History always contains at least the in-progress planner.
        assert any(
            row['id'] == planning_id for row in body['planning_task_history']
        )

    async def test_ready_state_after_approval(
        self, admin_client, install_fake_agent
    ):
        plan_text = '## final plan'
        install_fake_agent.default_scenario = FakeAgentScenario(plan=plan_text)
        sched = await _create_plan_schedule(admin_client)
        planning_id = await _wait_planning_task_id(admin_client, sched['id'])
        # Plan-only auto-approves at ExitPlanMode — just wait for
        # the schedule to land on 'ready'.
        await _wait_schedule_status(admin_client, sched['id'], 'ready')

        r = await admin_client.get(f'/api/schedules/{sched["id"]}/plan')
        body = r.json()
        assert body['plan_status'] == 'ready'
        assert body['plan_text'] == plan_text
        assert body['plan_version'] == 1
        assert body['current_planning_task_id'] is None
        # History keeps the completed planner for audit.
        completed = [
            row
            for row in body['planning_task_history']
            if row['id'] == planning_id
        ]
        assert completed and completed[0]['status'] == 'completed'

    async def test_stale_after_prompt_edit(
        self, admin_client, install_fake_agent
    ):
        install_fake_agent.default_scenario = FakeAgentScenario(plan='## v1')
        sched = await _create_plan_schedule(admin_client, description='first')
        await _wait_planning_task_id(admin_client, sched['id'])
        ready = await _wait_schedule_status(admin_client, sched['id'], 'ready')

        # Edit prompt → stale.
        r = await admin_client.put(
            f'/api/schedules/{sched["id"]}',
            json={
                'description': 'very different prompt',
                'plan_version': ready['plan_version'],
            },
        )
        assert r.status_code == 200

        body = (
            await admin_client.get(f'/api/schedules/{sched["id"]}/plan')
        ).json()
        assert body['plan_status'] == 'stale'
        # Old plan text preserved for diff display.
        assert body['plan_text'] == '## v1'
        assert body['current_planning_task_id'] is None

    async def test_replan_adds_to_history(
        self, admin_client, install_fake_agent
    ):
        install_fake_agent.default_scenario = FakeAgentScenario(plan='## v1')
        sched = await _create_plan_schedule(admin_client)
        planning_id = await _wait_planning_task_id(admin_client, sched['id'])
        await _wait_schedule_status(admin_client, sched['id'], 'ready')

        r = await admin_client.post(f'/api/schedules/{sched["id"]}/replan')
        assert r.status_code == 200
        new_planning_id = await _wait_planning_task_id(
            admin_client, sched['id']
        )
        assert new_planning_id != planning_id

        body = (
            await admin_client.get(f'/api/schedules/{sched["id"]}/plan')
        ).json()
        assert body['plan_status'] == 'planning'
        assert body['current_planning_task_id'] == new_planning_id
        history_ids = {row['id'] for row in body['planning_task_history']}
        # Both planners appear in the history.
        assert planning_id in history_ids
        assert new_planning_id in history_ids

    async def test_404_for_missing_schedule(
        self, admin_client, install_fake_agent
    ):
        r = await admin_client.get(
            '/api/schedules/00000000-0000-0000-0000-000000000000/plan'
        )
        assert r.status_code == 404
