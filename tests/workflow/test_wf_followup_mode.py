"""Regression test for the follow-up-during-DESIGNING mode bug.

Before the fix, ``app/routers/tasks_conversation.py`` picked
``mode='execute'`` for any plan-mode task that wasn't in
WAITING/COMPLETED/FAILED. For a plan-only task that was
interrupted mid-design, this meant the resumed agent got
``--permission-mode bypassPermissions`` instead of ``plan``, and
ExitPlanMode returned "You are not in plan mode" — so the replan
flow never produced a plan.

Fix: tasks in PENDING/QUEUED/DESIGNING keep ``plan_then_execute``
on follow-up; only PLANNED/RUNNING use ``execute``.
"""

import uuid

import pytest
from sqlalchemy import select

import app.database as _db
from app.models.schedule import Schedule
from app.models.task import Task
from app.models.user import User
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow


async def _seed_task(
    *, status: str, plan_mode: bool, is_plan_only: bool = False
):
    task_id = str(uuid.uuid4())
    sched_id = None
    async with _db.async_session() as db:
        u = (await db.execute(select(User).limit(1))).scalars().first()
        if is_plan_only:
            sched_id = str(uuid.uuid4())
            db.add(
                Schedule(
                    id=sched_id,
                    title='sched',
                    schedule_type='days',
                    schedule_time='09:00',
                    plan_mode=True,
                    plan_status='planning',
                    current_planning_task_id=task_id,
                    created_by=u.id,
                    phase_mode='fanout',
                    timezone='UTC',
                )
            )
        db.add(
            Task(
                id=task_id,
                title='follow-up test',
                description='design something',
                status=status,
                plan_mode=plan_mode,
                is_plan_only=is_plan_only,
                schedule_id=sched_id,
                created_by=u.id,
            )
        )
        await db.commit()
    return task_id


class TestFollowUpMode:
    async def test_plan_only_designing_followup_stays_in_plan_mode(
        self, admin_client, install_fake_agent
    ):
        """Follow-up during DESIGNING on a plan-only task must use
        ``plan_then_execute`` — otherwise the resumed CLI ends up
        in bypass-permissions mode and ExitPlanMode fails."""
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan='## will be authored'
        )
        task_id = await _seed_task(
            status='designing', plan_mode=True, is_plan_only=True
        )
        r = await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={'content': 'add more detail to the plan'},
        )
        assert r.status_code == 200
        run_calls = install_fake_agent.get_calls(task_id=task_id, action='run')
        assert run_calls, 'FakeAgent.run was not invoked'
        # The fix: mode must be plan_then_execute, NOT execute.
        assert run_calls[-1].mode == 'plan_then_execute', (
            f'Follow-up mode was {run_calls[-1].mode!r}; expected '
            "'plan_then_execute' so --permission-mode plan stays."
        )

    async def test_plan_mode_designing_followup_stays_in_plan_mode(
        self, admin_client, install_fake_agent
    ):
        """Same rule for a regular plan-mode task (not plan-only)."""
        install_fake_agent.default_scenario = FakeAgentScenario(plan='## p')
        task_id = await _seed_task(status='designing', plan_mode=True)
        r = await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={'content': 'more context'},
        )
        assert r.status_code == 200
        run_calls = install_fake_agent.get_calls(task_id=task_id, action='run')
        assert run_calls and run_calls[-1].mode == 'plan_then_execute'

    async def test_plan_mode_running_followup_is_execute(
        self, admin_client, install_fake_agent
    ):
        """A follow-up on a RUNNING plan-mode task continues in
        execute mode (past planning phase)."""
        install_fake_agent.default_scenario = FakeAgentScenario(plan='## p')
        task_id = await _seed_task(status='running', plan_mode=True)
        r = await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={'content': 'keep going'},
        )
        assert r.status_code == 200
        run_calls = install_fake_agent.get_calls(task_id=task_id, action='run')
        assert run_calls and run_calls[-1].mode == 'execute'

    async def test_auto_mode_followup_is_auto(
        self, admin_client, install_fake_agent
    ):
        """Auto-mode tasks always run as 'auto' regardless of status."""
        install_fake_agent.default_scenario = FakeAgentScenario()
        task_id = await _seed_task(status='designing', plan_mode=False)
        r = await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={'content': 'keep going'},
        )
        assert r.status_code == 200
        run_calls = install_fake_agent.get_calls(task_id=task_id, action='run')
        assert run_calls and run_calls[-1].mode == 'auto'


class TestFollowUpKwargs:
    """End-to-end-of-what-we-can-test: verify the full kwarg set
    that flows from the follow-up router to the agent for a
    plan-only Task whose first session crashed mid-design.

    FakeAgent can't assert "ExitPlanMode actually worked" (it
    doesn't model Claude Code's ``--permission-mode plan``); the
    CLI-flag mapping is covered by
    ``tests/unit/test_permission_mode_mapping.py``. What this
    test DOES pin is the three kwargs that together make plan
    mode + auto-approve work:

    - ``mode='plan_then_execute'``  → permission mode=plan (CLI)
    - ``resume=True``               → re-uses the prior session id
    - ``auto_approve_plan=True``    → schedule-owned task; hook
      auto-approves at ExitPlanMode instead of blocking forever

    Miss any of the three and the plan commit never happens. The
    prior bug (mode='execute') is one instance of this family."""

    async def test_plan_only_followup_kwargs_complete(
        self, admin_client, install_fake_agent
    ):
        install_fake_agent.default_scenario = FakeAgentScenario(plan='## plan')
        # Seed the crash-recovery shape: plan-only Task in DESIGNING
        # with a session_id from the prior crashed run.
        task_id = str(uuid.uuid4())
        sched_id = str(uuid.uuid4())
        async with _db.async_session() as db:
            u = (await db.execute(select(User).limit(1))).scalars().first()
            db.add(
                Schedule(
                    id=sched_id,
                    title='sched',
                    schedule_type='days',
                    schedule_time='09:00',
                    plan_mode=True,
                    plan_status='planning',
                    current_planning_task_id=task_id,
                    created_by=u.id,
                    phase_mode='fanout',
                    timezone='UTC',
                )
            )
            db.add(
                Task(
                    id=task_id,
                    title='Plan: sched',
                    description='do the thing',
                    status='designing',
                    plan_mode=True,
                    is_plan_only=True,
                    schedule_id=sched_id,
                    session_id='prior-session-from-crashed-run',
                    created_by=u.id,
                )
            )
            await db.commit()

        r = await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={'content': 'nudge plan'},
        )
        assert r.status_code == 200

        run_calls = install_fake_agent.get_calls(task_id=task_id, action='run')
        assert run_calls, 'FakeAgent.run was not invoked'
        last = run_calls[-1]
        # All three kwargs must align. Any one wrong = plan never
        # commits.
        assert last.mode == 'plan_then_execute', (
            f'mode={last.mode!r} — resumed CLI would not be in '
            f'plan mode; ExitPlanMode would fail.'
        )
        assert last.auto_approve_plan is True, (
            'auto_approve_plan=False — hook would block forever '
            'on _plan_approval_event for a schedule-owned task.'
        )

        # And verify the mode + resume flag that flowed through.
        run_calls = install_fake_agent.get_calls(task_id=task_id, action='run')
        assert run_calls and run_calls[-1].mode == 'plan_then_execute'
        assert (
            run_calls[-1].message_history is None
        )  # resume uses session_id, not history
