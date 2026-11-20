"""Schedules ignore ``user.plan_mode_default``.

That preference is the default execution mode of standalone tasks. A
schedule's mode is chosen on the schedule itself: auto unless the owner
ticks "review the plan" — the preference must not turn review on. And
for a review schedule the planner's ExitPlanMode is a hand-off to the
schedule (``pending_approval``) under either preference value: the
human decision happens on the schedule, never as a click on the
planning task. (The plan text is not screened at ExitPlanMode; the
fanout no-sub-tasks rule is enforced on the action, at
``POST /api/tasks`` — see
``tests/workflow/test_wf_fanout_subtask_guard.py``.)
"""

import asyncio

import pytest
from sqlalchemy import select

import app.database as _db
from app.models.schedule import Schedule
from app.models.user import User
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow


async def _set_admin_pref(value: bool) -> None:
    """Flip the single admin user's plan_mode_default."""
    async with _db.async_session() as db:
        u = (await db.execute(select(User).limit(1))).scalars().first()
        assert u is not None
        u.plan_mode_default = value
        await db.commit()


async def _wait_plan_status(sched_id: str, target: str, timeout=3.0):
    for _ in range(int(timeout / 0.05)):
        async with _db.async_session() as db:
            s = (
                await db.execute(
                    select(Schedule).where(Schedule.id == sched_id)
                )
            ).scalar_one_or_none()
            if s is not None and s.plan_status == target:
                return s
        await asyncio.sleep(0.05)
    return None


async def _create_schedule(client, **extra):
    r = await client.post(
        '/api/schedules',
        json={
            'title': 'T',
            'description': 'd',
            'schedule_type': 'days',
            'schedule_time': '09:00',
            **extra,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


class TestPlanModeUserPrefIgnored:
    @pytest.mark.parametrize('pref_value', [True, False])
    async def test_pref_does_not_turn_review_on(
        self, admin_client, install_fake_agent, pref_value
    ):
        await _set_admin_pref(pref_value)
        sched = await _create_schedule(admin_client)
        assert sched['plan_mode'] is False
        assert sched['plan_status'] == 'none'

    @pytest.mark.parametrize('pref_value', [True, False])
    async def test_review_planner_hands_over_regardless_of_pref(
        self, admin_client, install_fake_agent, pref_value
    ):
        """Both pref values end at plan_status='pending_approval' with
        no click on the planning task; nothing is approved for the
        owner."""
        await _set_admin_pref(pref_value)
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan='## proposed plan'
        )
        sched_id = (await _create_schedule(admin_client, plan_mode=True))['id']
        pending = await _wait_plan_status(
            sched_id, 'pending_approval', timeout=3.0
        )
        assert pending is not None, (
            f'The planner should hand its plan over even when '
            f'plan_mode_default={pref_value!r}.'
        )
        assert pending.proposed_plan == '## proposed plan'
        assert pending.plan is None
        assert pending.plan_version == 1
        assert pending.current_planning_task_id is None
