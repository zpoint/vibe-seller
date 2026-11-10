"""Workflow tests for plan-at-creation (Schedule plan lifecycle).

Covers the happy path and core edge cases for creating a plan-mode
schedule. When plan_mode=True, the POST spawns a plan-only Task and
leaves the Schedule in ``plan_status=planning`` until the agent calls
ExitPlanMode and the user approves.  On approval the plan is frozen
onto the Schedule and the fire-gate opens (plan_status=ready).
"""

import asyncio

import pytest
from sqlalchemy import select

import app.database as _db
from app.models.schedule import Schedule
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow


async def _wait_schedule_status(client, schedule_id, target, timeout=5.0):
    """Poll DB direct — API-mediated polling races with FakeAgent's
    background commit under StaticPool (see matching helper in
    tests/workflow/test_wf_schedule_plan_endpoint.py)."""
    for _ in range(int(timeout / 0.05)):
        async with _db.async_session() as db:
            s = (
                await db.execute(
                    select(Schedule).where(Schedule.id == schedule_id)
                )
            ).scalar_one_or_none()
            if s is not None and s.plan_status == target:
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


async def _wait_planning_task(client, schedule_id, timeout=5.0):
    """Return the current planning Task once it is PENDING/PLANNED/COMPLETED."""
    for _ in range(int(timeout / 0.05)):
        sched = (await client.get(f'/api/schedules/{schedule_id}')).json()
        tid = sched.get('current_planning_task_id')
        if tid:
            t = (await client.get(f'/api/tasks/{tid}')).json()
            return t
        await asyncio.sleep(0.05)
    raise AssertionError('No planning task appeared')


class TestCreatePlanMode:
    async def test_plan_mode_schedule_starts_in_planning(
        self, admin_client, install_fake_agent
    ):
        """POST a plan_mode schedule → status planning, planning task spawned."""
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan='## Daily product check\n1. List URLs\n2. Verify cart',
            design_delay=0.1,  # hold in DESIGNING long enough to observe
        )
        r = await admin_client.post(
            '/api/schedules',
            json={
                'title': 'Daily link check',
                'description': 'Check product links for anomalies',
                'schedule_type': 'days',
                'schedule_time': '09:05',
                'plan_mode': True,
            },
        )
        assert r.status_code == 201, r.text
        sched = r.json()
        assert sched['plan_mode'] is True
        assert sched['plan_status'] == 'planning'
        assert sched['plan_version'] == 0

        task = await _wait_planning_task(admin_client, sched['id'])
        assert task['status'] in {
            'pending',
            'designing',
            'planned',
            'completed',
        }
        # is_plan_only is not in the TaskResponse schema today — confirm
        # via the direct DB check via the schedule pointer.
        assert task['schedule_id'] == sched['id']

    async def test_exit_plan_mode_commits_plan_to_schedule(
        self, admin_client, install_fake_agent
    ):
        """ExitPlanMode → hook auto-commits plan to Schedule,
        Task ends COMPLETED. No user click required — the gate
        `bool(task.schedule_id)` auto-approves for plan-only."""
        plan_text = '## Canonical plan\n1. List products\n2. Push anomalies'
        install_fake_agent.default_scenario = FakeAgentScenario(plan=plan_text)

        r = await admin_client.post(
            '/api/schedules',
            json={
                'title': 'Link check',
                'description': 'Check product links',
                'schedule_type': 'days',
                'schedule_time': '09:05',
                'plan_mode': True,
            },
        )
        sched_id = r.json()['id']
        task = await _wait_planning_task(admin_client, sched_id)
        task_id = task['id']

        # Plan-only tasks auto-approve at ExitPlanMode — wait for
        # the schedule to land at plan_status='ready' directly.
        sched = await _wait_schedule_status(admin_client, sched_id, 'ready')
        assert sched['plan'] == plan_text
        assert sched['plan_version'] == 1
        assert sched['current_planning_task_id'] is None

        task_after = (await admin_client.get(f'/api/tasks/{task_id}')).json()
        assert task_after['status'] == 'completed'

    async def test_post_without_plan_mode_in_body_still_plans(
        self, admin_client, install_fake_agent
    ):
        """Regression guard for the #136 frontend-bypass bug.

        The real CreateScheduleModal does NOT send `plan_mode` in the
        POST body at all. Before the fix, the Pydantic default of
        False silently bypassed the plan-at-creation flow, so no
        schedule created through the UI ever got a planning task.

        This test sends the exact minimal body the frontend sends
        today and asserts the server still forces plan_mode=True and
        spawns a planning task.
        """
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan='## plan',
            design_delay=0.1,
        )
        r = await admin_client.post(
            '/api/schedules',
            json={
                # NO plan_mode field, mimicking the real UI.
                'title': 'UI-created schedule',
                'description': 'What the UI actually sends',
                'schedule_type': 'days',
                'schedule_time': '09:05',
            },
        )
        assert r.status_code == 201, r.text
        sched = r.json()
        assert sched['plan_mode'] is True
        assert sched['plan_status'] == 'planning'
        assert sched['current_planning_task_id'] is not None

    async def test_post_with_plan_mode_false_overridden(
        self, admin_client, install_fake_agent
    ):
        """An old client sending plan_mode=False is force-overridden.

        We don't 400 on it — keep the API tolerant — but the resulting
        schedule must still go through the plan lifecycle. This
        prevents a quiet regression if some caller hard-codes
        plan_mode=False.
        """
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan='## plan',
            design_delay=0.1,
        )
        r = await admin_client.post(
            '/api/schedules',
            json={
                'title': 'Legacy client',
                'description': 'd',
                'schedule_type': 'days',
                'schedule_time': '09:00',
                'plan_mode': False,  # explicitly False
            },
        )
        assert r.status_code == 201
        sched = r.json()
        assert sched['plan_mode'] is True, (
            'Server must force plan_mode=True for user-created schedules'
        )
        assert sched['plan_status'] == 'planning'
        assert sched['current_planning_task_id'] is not None
