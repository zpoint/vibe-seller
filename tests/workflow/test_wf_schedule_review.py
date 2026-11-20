"""Workflow tests for plan review: approve / request changes / attention,
and what a fire runs.

Pins the invariants of the opt-in review design (docs/subsystems.md
§ Plan review):

* I1 — auto schedules fire their prompt; a fire never carries a plan
  left on the row (the mode is read from the row at fire time);
* I2 — a review schedule fires only a plan a human approved; the
  in-task agent (ai_bot) can never approve or rewrite it; approval is
  a compare-and-swap on the version the reviewer read;
* an approved plan keeps firing while a revision is in progress;
* GET /attention lists exactly the plans waiting for approval.
"""

import asyncio
import json
import uuid

import pytest
from sqlalchemy import select

import app.database as _db
from app.models.schedule import Schedule
from app.models.task import Task
from app.models.user import User
from app.schedule_plan_review import record_plan_proposal
from app.scheduler.fanout import create_manual_trigger_task, run_single_job
from tests.workflow.fake_agent import FakeAgentScenario
from tests.workflow.schedule_review_helpers import (
    approve_when_proposed,
    wait_plan_status,
)

pytestmark = pytest.mark.workflow


async def _row(**fields) -> str:
    """Insert a schedule row directly (no planner races)."""
    sched_id = str(uuid.uuid4())
    async with _db.async_session() as db:
        user = (await db.execute(select(User).limit(1))).scalars().first()
        db.add(
            Schedule(
                id=sched_id,
                title='t',
                description='the prompt',
                schedule_type='days',
                schedule_time='09:05',
                created_by=user.id,
                phase_mode='single',
                timezone='UTC',
                **fields,
            )
        )
        await db.commit()
    return sched_id


async def _tasks(sched_id):
    async with _db.async_session() as db:
        rows = await db.execute(
            select(Task).where(
                Task.schedule_id == sched_id, Task.is_plan_only.is_(False)
            )
        )
        return rows.scalars().all()


async def _review_pending(admin_client, plan='## proposed'):
    """A review schedule created over the API, its plan pending."""
    r = await admin_client.post(
        '/api/schedules',
        json={
            'title': 'Review me',
            'description': 'do it',
            'schedule_type': 'days',
            'schedule_time': '09:00',
            'plan_mode': True,
        },
    )
    assert r.status_code == 201, r.text
    sched_id = r.json()['id']
    pending = await wait_plan_status(sched_id, 'pending_approval')
    return sched_id, pending


class TestWhatAFireRuns:
    async def test_auto_fire_never_carries_a_leftover_plan(
        self, admin_client, install_fake_agent
    ):
        """A schedule switched to auto with text still on the row (or a
        job registered while it was a review schedule) fires the PROMPT:
        the mode comes from the row, never from the job kwargs."""
        sched_id = await _row(
            plan_mode=False, plan_status='none', plan='old frozen plan'
        )
        await run_single_job(
            schedule_id=sched_id,
            task_title='t',
            description='the prompt',
            plan_mode=True,  # stale job kwargs
        )
        (task,) = await _tasks(sched_id)
        assert task.plan is None
        assert task.plan_mode is False
        assert task.status != 'planned'

    @pytest.mark.parametrize(
        'plan_status', ['planning', 'pending_approval', 'failed']
    )
    async def test_approved_plan_keeps_firing_during_a_revision(
        self, admin_client, install_fake_agent, plan_status
    ):
        sched_id = await _row(
            plan_mode=True,
            plan_status=plan_status,
            plan='the approved plan',
            plan_version=3,
        )
        await run_single_job(
            schedule_id=sched_id,
            task_title='t',
            description='d',
            plan_mode=True,
        )
        (task,) = await _tasks(sched_id)
        assert task.plan == 'the approved plan'
        assert task.status == 'planned'

    @pytest.mark.parametrize(
        'plan_status', ['planning', 'pending_approval', 'failed', 'stale']
    )
    async def test_no_approved_plan_never_fires(
        self, admin_client, install_fake_agent, plan_status
    ):
        sched_id = await _row(
            plan_mode=True,
            plan_status=plan_status,
            proposed_plan='a proposal nobody approved',
        )
        r = await admin_client.post(f'/api/schedules/{sched_id}/trigger')
        assert r.status_code == 409
        await run_single_job(
            schedule_id=sched_id,
            task_title='t',
            description='d',
            plan_mode=True,
        )
        assert await _tasks(sched_id) == []

    async def test_manual_trigger_task_follows_the_row(
        self, admin_client, install_fake_agent
    ):
        sched_id = await _row(
            plan_mode=False, plan_status='none', plan='leftover'
        )
        async with _db.async_session() as db:
            sched = await db.get(Schedule, sched_id)
            task = await create_manual_trigger_task(sched, sched.created_by, db)
        assert task.plan is None
        assert task.plan_mode is False


class TestApprove:
    async def test_approve_moves_the_proposal_into_the_plan(
        self, admin_client, install_fake_agent
    ):
        install_fake_agent.default_scenario = FakeAgentScenario(plan='## P')
        sched_id, pending = await _review_pending(admin_client)
        r = await admin_client.post(
            f'/api/schedules/{sched_id}/plan/approve',
            json={'plan_version': pending['plan_version']},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['plan_status'] == 'ready'
        assert body['plan'] == '## P'
        assert body['proposed_plan'] is None
        assert body['plan_version'] == pending['plan_version'] + 1

    async def test_approving_twice_is_refused(
        self, admin_client, install_fake_agent
    ):
        install_fake_agent.default_scenario = FakeAgentScenario(plan='## P')
        sched_id, _ = await _review_pending(admin_client)
        await approve_when_proposed(admin_client, sched_id)
        r = await admin_client.post(f'/api/schedules/{sched_id}/plan/approve')
        assert r.status_code == 409

    async def test_stale_version_is_refused(
        self, admin_client, install_fake_agent
    ):
        install_fake_agent.default_scenario = FakeAgentScenario(plan='## P')
        sched_id, pending = await _review_pending(admin_client)
        r = await admin_client.post(
            f'/api/schedules/{sched_id}/plan/approve',
            json={'plan_version': pending['plan_version'] - 1},
        )
        assert r.status_code == 412
        row = await wait_plan_status(sched_id, 'pending_approval')
        assert row['plan'] is None

    async def test_approve_after_an_edit_restarted_planning_is_refused(
        self, admin_client, install_fake_agent
    ):
        """The reviewer read version N; an edit then started a new
        planner. Approving N must not freeze a plan for the old
        prompt (the compare-and-swap refuses it)."""
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan='## P', design_delay=0.5
        )
        sched_id, pending = await _review_pending(admin_client)
        r = await admin_client.put(
            f'/api/schedules/{sched_id}', json={'description': 'new prompt'}
        )
        assert r.json()['plan_status'] == 'planning'
        r = await admin_client.post(
            f'/api/schedules/{sched_id}/plan/approve',
            json={'plan_version': pending['plan_version']},
        )
        assert r.status_code in (409, 412)
        async with _db.async_session() as db:
            row = await db.get(Schedule, sched_id)
            assert row.plan is None
            assert row.current_planning_task_id is not None
        await wait_plan_status(sched_id, 'pending_approval')

    async def test_task_agent_cannot_approve(
        self, admin_client, ai_bot_client, install_fake_agent
    ):
        install_fake_agent.default_scenario = FakeAgentScenario(plan='## P')
        sched_id, _ = await _review_pending(admin_client)
        r = await ai_bot_client.post(f'/api/schedules/{sched_id}/plan/approve')
        assert r.status_code == 403, r.text
        r = await ai_bot_client.post(
            f'/api/schedules/{sched_id}/plan/request-changes',
            json={'feedback': 'do something else'},
        )
        assert r.status_code == 403, r.text
        row = await wait_plan_status(sched_id, 'pending_approval')
        assert row['plan'] is None

    async def test_auto_schedule_has_nothing_to_approve(
        self, admin_client, install_fake_agent
    ):
        sched_id = await _row(plan_mode=False, plan_status='none')
        r = await admin_client.post(f'/api/schedules/{sched_id}/plan/approve')
        assert r.status_code == 400


class TestRequestChanges:
    async def test_feedback_reaches_the_new_planner(
        self, admin_client, install_fake_agent
    ):
        install_fake_agent.default_scenario = FakeAgentScenario(plan='## v1')
        sched_id, pending = await _review_pending(admin_client)
        r = await admin_client.post(
            f'/api/schedules/{sched_id}/plan/request-changes',
            json={
                'feedback': 'Skip weekends',
                'plan_version': pending['plan_version'],
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['plan_status'] in ('planning', 'pending_approval')
        assert body['plan_feedback'] == 'Skip weekends'
        planner = body['current_planning_task_id']
        for _ in range(100):
            calls = install_fake_agent.get_calls(task_id=planner, action='run')
            if calls:
                break
            await asyncio.sleep(0.05)
        assert calls and 'Skip weekends' in calls[0].system_extra
        assert '## v1' in calls[0].system_extra

    async def test_empty_feedback_is_refused(
        self, admin_client, install_fake_agent
    ):
        install_fake_agent.default_scenario = FakeAgentScenario(plan='## v1')
        sched_id, _ = await _review_pending(admin_client)
        r = await admin_client.post(
            f'/api/schedules/{sched_id}/plan/request-changes',
            json={'feedback': '  '},
        )
        assert r.status_code == 400

    async def test_nothing_to_revise_while_planning(
        self, admin_client, install_fake_agent
    ):
        sched_id = await _row(plan_mode=True, plan_status='planning')
        r = await admin_client.post(
            f'/api/schedules/{sched_id}/plan/request-changes',
            json={'feedback': 'x'},
        )
        assert r.status_code == 409

    async def test_revising_a_ready_plan_keeps_it_firing(
        self, admin_client, install_fake_agent
    ):
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan='## v1', design_delay=0.5
        )
        sched_id, _ = await _review_pending(admin_client)
        await approve_when_proposed(admin_client, sched_id)
        r = await admin_client.post(
            f'/api/schedules/{sched_id}/plan/request-changes',
            json={'feedback': 'tighter'},
        )
        assert r.status_code == 200
        assert r.json()['plan'] == '## v1'
        r = await admin_client.post(f'/api/schedules/{sched_id}/trigger')
        assert r.status_code == 200, r.text
        await wait_plan_status(sched_id, 'pending_approval')


class TestProposalHandOff:
    async def test_superseded_planner_cannot_propose(
        self, admin_client, install_fake_agent
    ):
        """A planner whose schedule moved on (an edit started a newer
        one) ends FAILED and its plan never lands."""
        sched_id = await _row(
            plan_mode=True,
            plan_status='planning',
            current_planning_task_id='someone-else',
        )
        async with _db.async_session() as db:
            user = (await db.execute(select(User).limit(1))).scalars().first()
            task = Task(
                title='old planner',
                created_by=user.id,
                schedule_id=sched_id,
                status='designing',
                plan_mode=True,
                is_plan_only=True,
            )
            db.add(task)
            await db.commit()
            task_id = task.id
        landed = await record_plan_proposal(task_id, sched_id, 'stale plan')
        assert landed is False
        async with _db.async_session() as db:
            row = await db.get(Schedule, sched_id)
            t = await db.get(Task, task_id)
        assert row.proposed_plan is None
        assert row.plan_status == 'planning'
        assert t.status == 'failed'
        assert t.error_category == 'superseded_by_edit'


class TestAttention:
    async def test_lists_only_pending_approvals(
        self, admin_client, install_fake_agent
    ):
        install_fake_agent.default_scenario = FakeAgentScenario(plan='## P')
        sched_id, pending = await _review_pending(admin_client)
        await _row(plan_mode=True, plan_status='ready', plan='x')
        await _row(plan_mode=False, plan_status='none')
        r = await admin_client.get('/api/schedules/attention')
        assert r.status_code == 200, r.text
        items = r.json()['plan_approvals']
        assert [i['schedule_id'] for i in items] == [sched_id]
        assert items[0]['plan_version'] == pending['plan_version']
        await approve_when_proposed(admin_client, sched_id)
        r = await admin_client.get('/api/schedules/attention')
        assert r.json()['plan_approvals'] == []


class TestPlannerQuestions:
    async def test_attention_lists_a_planner_waiting_on_an_answer(
        self, admin_client, install_fake_agent
    ):
        """A planner that asked the owner a question cannot finish the
        plan until it is answered — the banner must say so."""
        sched_id = await _row(plan_mode=True, plan_status='planning')
        async with _db.async_session() as db:
            user = (await db.execute(select(User).limit(1))).scalars().first()
            asking = Task(
                title='planner',
                created_by=user.id,
                schedule_id=sched_id,
                status='waiting',
                plan_mode=True,
                is_plan_only=True,
                wait_condition=json.dumps({
                    'pending_question': {
                        'request_id': 'r1',
                        'questions': [{'question': 'Which source?'}],
                    }
                }),
            )
            db.add(asking)
            await db.flush()
            row = await db.get(Schedule, sched_id)
            row.current_planning_task_id = asking.id
            await db.commit()
            task_id = asking.id
        r = await admin_client.get('/api/schedules/attention')
        assert r.status_code == 200, r.text
        assert r.json()['planner_questions'] == [
            {
                'schedule_id': sched_id,
                'title': 't',
                'store_id': None,
                'task_id': task_id,
            }
        ]


class TestFailureCode:
    async def test_a_planner_that_cannot_start_says_so(
        self, admin_client, install_fake_agent, monkeypatch
    ):
        """The schedule records WHY (a code the UI translates), not only
        the technical text."""

        import app.routers.schedule_planning as planning  # noqa: PLC0415

        launch = planning.schedule_or_run

        async def _boom(*args, **kwargs):
            raise RuntimeError('launcher exploded')

        monkeypatch.setattr(planning, 'schedule_or_run', _boom)
        r = await admin_client.post(
            '/api/schedules',
            json={
                'title': 'Cannot start',
                'description': 'd',
                'schedule_type': 'days',
                'schedule_time': '09:00',
                'plan_mode': True,
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body['plan_status'] == 'failed'
        assert body['plan_error_code'] == 'start_failed'
        assert 'launcher exploded' in body['plan_error']
        plan = (
            await admin_client.get(f'/api/schedules/{body["id"]}/plan')
        ).json()
        assert plan['plan_error_code'] == 'start_failed'

        # Re-planning clears it.
        monkeypatch.setattr(planning, 'schedule_or_run', launch)
        r = await admin_client.post(f'/api/schedules/{body["id"]}/replan')
        assert r.json()['plan_error_code'] is None
        await wait_plan_status(body['id'], 'pending_approval')
