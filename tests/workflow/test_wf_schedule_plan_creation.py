"""Workflow tests for creating schedules: auto by default, review on ask.

A schedule is AUTO unless the owner asks to review the plan
(``plan_mode=True``). Then the POST spawns a plan-only Task and leaves
the Schedule in ``plan_status=planning``; when the agent calls
ExitPlanMode the plan becomes a PROPOSAL (``pending_approval``), and
only the owner's approval makes it the plan every fire runs
(``ready``).
"""

import asyncio

import pytest
from sqlalchemy import select

import app.database as _db
from app.models.schedule import Schedule
from app.models.task import Task
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

    async def test_exit_plan_mode_proposes_and_only_approval_commits(
        self, admin_client, install_fake_agent
    ):
        """ExitPlanMode → the plan is PROPOSED, the planning Task ends
        COMPLETED, and nothing is approved until the owner says so."""
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

        sched = await _wait_schedule_status(
            admin_client, sched_id, 'pending_approval'
        )
        assert sched['plan'] is None, 'a proposal is never the run plan'
        assert sched['plan_version'] == 1
        assert sched['current_planning_task_id'] is None
        body = (await admin_client.get(f'/api/schedules/{sched_id}')).json()
        assert body['proposed_plan'] == plan_text

        task_after = (await admin_client.get(f'/api/tasks/{task_id}')).json()
        assert task_after['status'] == 'completed'

        # The owner approves the version they read.
        r = await admin_client.post(
            f'/api/schedules/{sched_id}/plan/approve',
            json={'plan_version': 1},
        )
        assert r.status_code == 200, r.text
        approved = r.json()
        assert approved['plan_status'] == 'ready'
        assert approved['plan'] == plan_text
        assert approved['proposed_plan'] is None
        assert approved['plan_version'] == 2

    async def test_post_without_plan_mode_in_body_is_auto(
        self, admin_client, install_fake_agent
    ):
        """The minimal body (no plan_mode) creates an AUTO schedule: no
        planner, nothing to approve, it fires its prompt."""
        r = await admin_client.post(
            '/api/schedules',
            json={
                'title': 'UI-created schedule',
                'description': 'What the UI sends by default',
                'schedule_type': 'days',
                'schedule_time': '09:05',
            },
        )
        assert r.status_code == 201, r.text
        sched = r.json()
        assert sched['plan_mode'] is False
        assert sched['plan_status'] == 'none'
        assert sched['current_planning_task_id'] is None
        async with _db.async_session() as db:
            planners = (
                (
                    await db.execute(
                        select(Task).where(Task.schedule_id == sched['id'])
                    )
                )
                .scalars()
                .all()
            )
        assert planners == []

    async def test_post_with_plan_mode_false_is_honoured(
        self, admin_client, install_fake_agent
    ):
        """plan_mode=False is the owner's choice, not overridden."""
        r = await admin_client.post(
            '/api/schedules',
            json={
                'title': 'Explicit auto',
                'description': 'd',
                'schedule_type': 'days',
                'schedule_time': '09:00',
                'plan_mode': False,
            },
        )
        assert r.status_code == 201
        sched = r.json()
        assert sched['plan_mode'] is False
        assert sched['plan_status'] == 'none'
        assert sched['current_planning_task_id'] is None
