"""Schedule plan-only authoring auto-approves regardless of
``user.plan_mode_default``.

The schedule plan-authoring flow is intentionally decoupled from
the user-level ``plan_mode_default`` preference so the approve UX
is consistent across schedules: plan is validated by the
fanout-plan guardrail (see
``_validate_fanout_plan_text`` in ``claude_backend_hooks.py``),
then auto-committed to the schedule. Users review + iterate via
the SchedulePlanPanel (Re-plan / edit prompt) rather than a
blocking "approve" click.

This test is the opposite of an earlier iteration where the
preference gated the approval path — asserts that BOTH values now
land the plan on the schedule without any UI click.
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


async def _create_schedule(client):
    r = await client.post(
        '/api/schedules',
        json={
            'title': 'T',
            'description': 'd',
            'schedule_type': 'days',
            'schedule_time': '09:00',
        },
    )
    assert r.status_code == 201, r.text
    return r.json()['id']


class TestPlanModeUserPrefIgnored:
    @pytest.mark.parametrize('pref_value', [True, False])
    async def test_schedule_plan_auto_commits_regardless_of_pref(
        self, admin_client, install_fake_agent, pref_value
    ):
        """Both pref values end at plan_status='ready' without a
        user click. The gate is `bool(task.schedule_id)`, not the
        creator's preference."""
        await _set_admin_pref(pref_value)
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan='## auto-approved plan'
        )
        sched_id = await _create_schedule(admin_client)
        ready = await _wait_plan_status(sched_id, 'ready', timeout=3.0)
        assert ready is not None, (
            f'Schedule should reach ready without a click even when '
            f'plan_mode_default={pref_value!r}.'
        )
        assert ready.plan == '## auto-approved plan'
        assert ready.plan_version == 1
        assert ready.current_planning_task_id is None
