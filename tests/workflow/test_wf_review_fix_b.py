"""Workflow tests for the PR-#194 review fixes (fix-b batch).

Each test here is the negative-control companion of a code change:
run against the pre-fix tree it fails, against the fixed tree it
passes. What they pin, per finding:

* S2 — a schedule-FIRED task at ``planned`` auto-executes its frozen
  plan: ``wait_task`` must NOT report ``needs_operator`` (and the
  guidance must not offer ``review_plan``) — the state is
  pre-execution, not a checkpoint.
* Q2 — ``create_schedule``'s pause step failing deletes the schedule
  it just created; nothing half-armed is left behind.
* S5 — retrying a completed PLAN-ONLY task re-authors the plan; it
  must not EXECUTE the frozen one (pre-fix, retry re-seeded the plan
  and spawned an EXECUTE session: the "planner" ran the store work).
* M6 — a planner that already died under a ``planning`` row gets
  replan guidance, not "wait for the planner".
* M7 — deleting a schedule mid-planning stops the in-flight planner
  (it must not park at ``planned`` holding an agent slot).
* M11 — a create_task whose upload/start chain fails deletes the
  PENDING task instead of leaving a never-running row behind.
* M12 — a second concurrent ``workspace_assistant(send)`` for one
  user is refused with a clear error.
* M13 — ``wait_task`` never sleeps past the caller's budget.
"""

import asyncio
import time

from fastapi import HTTPException
import pytest

from app.agent_mcp import tools_tasks
import app.database as _db
from app.models.schedule import Schedule
from app.models.task import Task
from app.routers import (
    attachments as attachments_route,
    schedules as schedules_route,
)
from tests.workflow.conftest import real_sleep
from tests.workflow.fake_agent import FakeAgentScenario
from tests.workflow.test_wf_agent_mcp import make_store
from tests.workflow.test_wf_agent_mcp_assistant import MockAssistantManager
from tests.workflow.test_wf_agent_mcp_schedules import (
    PLAN_TEXT,
    call_tool,
    wait_plan_status,
)

pytestmark = pytest.mark.workflow


@pytest.fixture
def mock_ws_assistant(monkeypatch):
    """The workspace-assistant mock, wired the same way the assistant
    workflow module wires it (same patch point, same event shapes)."""
    mock = MockAssistantManager()
    monkeypatch.setattr(
        'app.routers.workspace_assistant.ws_assistant_manager', mock
    )
    return mock


async def _schedule_row(schedule_id):
    async with _db.async_session() as db:
        return await db.get(Schedule, schedule_id)


async def _task_row(task_id):
    async with _db.async_session() as db:
        return await db.get(Task, task_id)


class TestScheduleFiredPlannedIsNotACheckpoint:
    """S2: the fired task's plan was accepted when the schedule was
    armed; reviewing (or messaging) it would discard that plan and
    re-plan with automatic approval."""

    async def test_wait_task_waits_through_fired_planned(
        self, admin_client, install_fake_agent
    ):
        # The queue does not dispatch without the app lifespan, so the
        # fired task sits at `planned` — exactly the window an agent
        # would call wait_task in.
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan=PLAN_TEXT, result='EXECUTED THE PLAN'
        )
        store_id = await make_store(admin_client, 'Fired Store')
        created, is_error = await call_tool(
            admin_client,
            'create_schedule',
            {
                'title': 'Fired weekly check',
                'description': 'Check links',
                'schedule_type': 'weekly',
                'schedule_time': '09:05',
                'store_id': store_id,
                'paused': False,
            },
        )
        assert not is_error, created
        await wait_plan_status(created['schedule_id'], 'ready')

        fired, is_error = await call_tool(
            admin_client, 'run_schedule', {'schedule_id': created['id']}
        )
        assert not is_error, fired
        task_id = fired['task_id']
        task = await _task_row(task_id)
        assert task.status == 'planned', (
            'precondition: the fired task is parked at planned'
        )

        waited, is_error = await call_tool(
            admin_client,
            'wait_task',
            {'task_id': task_id, 'timeout_seconds': 1},
        )
        assert not is_error, waited
        assert waited['reason'] == 'timeout', (
            'a schedule-fired planned task is pre-execution: the wait '
            'blocks, it must not answer needs_operator'
        )
        assert 'frozen plan' in waited['status_meaning']
        assert 'review_plan' not in waited['next_actions']
        assert 'wait_task' in waited['next_actions']
        # Clean up the parked task (it holds no agent; the delete
        # guard allows non-ACTIVE).
        await admin_client.delete(f'/api/tasks/{task_id}')

    async def test_get_task_guidance_matches(
        self, admin_client, install_fake_agent
    ):
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan=PLAN_TEXT, result='EXECUTED THE PLAN'
        )
        store_id = await make_store(admin_client, 'Fired Store 2')
        created, is_error = await call_tool(
            admin_client,
            'create_schedule',
            {
                'title': 'Fired weekly check',
                'description': 'Check links',
                'schedule_type': 'weekly',
                'schedule_time': '09:05',
                'store_id': store_id,
                'paused': False,
            },
        )
        await wait_plan_status(created['schedule_id'], 'ready')
        fired, is_error = await call_tool(
            admin_client, 'run_schedule', {'schedule_id': created['id']}
        )
        task_id = fired['task_id']

        got, is_error = await call_tool(
            admin_client, 'get_task', {'task_id': task_id}
        )
        assert not is_error, got
        assert 'review_plan' not in got['next_actions']
        assert 'frozen plan' in got['status_meaning']
        await admin_client.delete(f'/api/tasks/{task_id}')


class TestCreateSchedulePauseFailure:
    """Q2: a pause that fails must not leave an armed schedule."""

    async def test_failed_pause_deletes_the_created_schedule(
        self, admin_client, install_fake_agent, monkeypatch
    ):
        install_fake_agent.default_scenario = FakeAgentScenario(plan=PLAN_TEXT)

        async def _boom(*args, **kwargs):
            raise HTTPException(status_code=500, detail='pause exploded')

        monkeypatch.setattr(schedules_route, 'pause_schedule', _boom)
        result, is_error = await call_tool(
            admin_client,
            'create_schedule',
            {
                'title': 'Doomed schedule',
                'description': 'x',
                'schedule_type': 'days',
                'schedule_time': '08:00',
            },
        )
        assert is_error, result
        assert 'deleted' in result['error'], result
        assert 'nothing was left armed' in result['error']

        # Nothing half-created remains: the schedule is gone.
        listed, is_error = await call_tool(admin_client, 'list_schedules', {})
        assert not is_error, listed
        assert all(
            s['title'] != 'Doomed schedule' for s in listed['schedules']
        ), 'the failed-pause schedule must be deleted, not left active'


class TestRetryPlanOnlyReauthors:
    """S5: retry on a plan-only task re-authors — it must not run the
    frozen plan (pre-fix: execute_planned_task spawned an EXECUTE
    session, the planner "ran the store work" and stored a result)."""

    async def test_retry_reauthors_without_executing(
        self, admin_client, install_fake_agent
    ):
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan=PLAN_TEXT, result='EXECUTED THE PLAN'
        )
        created, is_error = await call_tool(
            admin_client,
            'create_schedule',
            {
                'title': 'Reauth weekly check',
                'description': 'Check links',
                'schedule_type': 'weekly',
                'schedule_time': '09:05',
                'paused': False,
            },
        )
        assert not is_error, created
        schedule_id = created['schedule_id']
        planner_id = created['plan_task_id']
        await wait_plan_status(schedule_id, 'ready')
        planner = await _task_row(planner_id)
        assert planner.status == 'completed'
        assert planner.result is None, 'the planner executed nothing'

        retried, is_error = await call_tool(
            admin_client, 'retry_task', {'task_id': planner_id}
        )
        assert not is_error, retried

        # The re-authored planner completes again WITHOUT executing:
        # its result stays empty and the plan re-freezes. (Pre-fix the
        # task ran an EXECUTE session and stored the scenario result.)
        for _ in range(200):
            await asyncio.sleep(0.05)
            planner = await _task_row(planner_id)
            if planner.status == 'completed':
                break
        assert planner.status == 'completed', planner.status
        assert planner.result is None, (
            'retry EXECUTED the frozen plan instead of re-authoring: '
            f'{planner.result!r}'
        )
        await wait_plan_status(schedule_id, 'ready')


class TestDeadPlannerGuidance:
    """M6: a planner that died under a `planning` row gets replan."""

    async def test_stopped_planner_names_replan_not_wait(
        self, admin_client, install_fake_agent
    ):
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan=PLAN_TEXT,
            design_delay=30.0,  # hold the planner in DESIGNING
        )
        created, is_error = await call_tool(
            admin_client,
            'create_schedule',
            {
                'title': 'Dead planner check',
                'description': 'x',
                'schedule_type': 'days',
                'schedule_time': '08:00',
            },
        )
        assert not is_error, created
        schedule_id = created['schedule_id']
        planner_id = created['plan_task_id']

        # The operator stops the stuck planner. The plan reaper (a
        # background loop) is not running under the test client, so
        # plan_status stays `planning` — the real-world window.
        stopped, is_error = await call_tool(
            admin_client, 'stop_task', {'task_id': planner_id}
        )
        assert not is_error, stopped
        row = await _schedule_row(schedule_id)
        assert row.plan_status == 'planning'

        listed, is_error = await call_tool(admin_client, 'list_schedules', {})
        entry = next(s for s in listed['schedules'] if s['id'] == schedule_id)
        assert entry['next_actions'] == ['update_schedule'], (
            'a dead planner must be answered with replan, not with '
            'wait_task on a planner that no longer exists'
        )
        assert 'replan' in entry['plan_status_meaning']
        assert 'ended' in entry['plan_status_meaning']


class TestDeleteScheduleStopsThePlanner:
    """M7: deleting mid-planning must not strand the planner at
    `planned` holding an agent slot."""

    async def test_delete_fails_the_in_flight_planner(
        self, admin_client, install_fake_agent
    ):
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan=PLAN_TEXT,
            design_delay=30.0,
        )
        created, is_error = await call_tool(
            admin_client,
            'create_schedule',
            {
                'title': 'Delete-mid-planning',
                'description': 'x',
                'schedule_type': 'days',
                'schedule_time': '08:00',
            },
        )
        planner_id = created['plan_task_id']
        row = await _task_row(planner_id)
        assert row.status in ('designing', 'pending', 'queued')

        deleted, is_error = await call_tool(
            admin_client,
            'delete_schedule',
            {'schedule_id': created['schedule_id']},
        )
        assert not is_error, deleted

        planner = await _task_row(planner_id)
        assert planner.status == 'failed', (
            'the in-flight planner must be stopped (FAILED), not left '
            f'designing/planned: {planner.status}'
        )
        assert planner.error == 'Schedule deleted.'


class TestCreateTaskCleanupOnUploadFailure:
    """M11: an upload/start failure deletes the deferred task."""

    async def test_failed_upload_deletes_the_pending_task(
        self, admin_client, install_fake_agent, monkeypatch
    ):
        async def _boom(*args, **kwargs):
            raise HTTPException(status_code=500, detail='disk full')

        monkeypatch.setattr(attachments_route, 'upload_attachment', _boom)
        attachment = {
            'filename': 'chart.png',
            'mime_type': 'image/png',
            'content_base64': (
                'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42'
                'mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='
            ),
        }
        result, is_error = await call_tool(
            admin_client,
            'create_task',
            {
                'description': 'Look at the chart',
                'attachments': [attachment],
            },
        )
        assert is_error, result
        assert 'deleted' in result['error'], result
        assert 'nothing half-created' in result['error']

        # No PENDING row left behind (the create response's task id
        # is gone — surfaced via the error message, so assert the
        # task list has no half-created task with that description).
        listed, is_error = await call_tool(
            admin_client,
            'list_tasks',
            {'limit': 200},
        )
        assert not is_error, listed
        assert all(
            t.get('title') != 'Look at the chart' for t in listed['tasks']
        ), 'the failed create must not leave a PENDING task behind'


class TestWorkspaceAssistantOneSendAtATime:
    """M12: a second concurrent send for one user is refused."""

    async def test_second_send_while_one_waits_is_refused(
        self, admin_client, mock_ws_assistant
    ):
        # Hold the per-user send lock the way an in-flight send would,
        # then prove the tool refuses instead of racing two collectors.
        me = (await admin_client.get('/api/auth/me')).json()
        lock = mock_ws_assistant.send_lock(me['id'])
        async with lock:
            refused, is_error = await call_tool(
                admin_client,
                'workspace_assistant',
                {'action': 'send', 'message': 'second question'},
            )
        assert is_error, refused
        assert 'still waiting' in refused['error'], refused['error']

        # With the lock free again the same send goes through.
        ok, is_error = await call_tool(
            admin_client,
            'workspace_assistant',
            {'action': 'send', 'message': 'hello there'},
        )
        assert not is_error, ok
        assert ok['done'] is True
        assert ok['reply'] == 'Done'


class TestWaitTaskBudget:
    """M13: the poll never sleeps past the caller's timeout."""

    async def test_no_overshoot_past_the_deadline(
        self, admin_client, install_fake_agent, monkeypatch
    ):
        # The admin_client fixture speeds asyncio.sleep up to 10 ms
        # (fast_polling), which would neutralise the very timing this
        # test measures — restore the REAL sleep for its duration.
        # Nothing else sleeps here: the task is deferred (no agent)
        # and the poll's only work is the patched question lookup.
        monkeypatch.setattr(asyncio, 'sleep', real_sleep)

        # A PENDING task (deferred, never started) never wakes.
        created = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Never starts',
                'description': 'x',
                'defer_start': True,
            },
        )
        task_id = created.json()['id']

        # Make each poll iteration expensive, so a fixed 1 s sleep
        # after a check that already burned most of the budget
        # visibly overshoots the 1 s budget.
        real_pending = tools_tasks.pending_question_for

        async def _slow_pending(ctx, task):
            await real_sleep(0.6)
            return await real_pending(ctx, task)

        monkeypatch.setattr(tools_tasks, 'pending_question_for', _slow_pending)

        started = time.monotonic()
        result, is_error = await call_tool(
            admin_client,
            'wait_task',
            {'task_id': task_id, 'timeout_seconds': 1},
        )
        elapsed = time.monotonic() - started
        assert not is_error, result
        assert result['reason'] == 'timeout'
        assert elapsed < 1.5, (
            f'wait_task overshot its 1 s budget by '
            f'{elapsed - 1:.2f} s — the poll must sleep '
            'min(remaining, interval), not a fixed interval'
        )
        await admin_client.delete(f'/api/tasks/{task_id}')
