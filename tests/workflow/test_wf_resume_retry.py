"""Workflow tests for orchestrator-owned resume-failure retry.

These pin the wiring across all four lifecycle entrypoints into
`app.task_session_lifecycle.wait_for_session_with_retry`:

- ``execute_woken_task``       — wake a WAITING task via /messages
- ``finalize_followup_session`` — follow-up on a COMPLETED task
- ``auto_run_task``            — fresh task with a stale session_id

The original bug: only `auto_run_task` and `finalize_followup_session`
were wired into `_maybe_retry_without_resume`; ``execute_woken_task``
fell straight through to FAILED on a stale ``--resume`` rejection.
Each test below would have caught that miss for its respective path
because asserting the second `run` (or `retry_without_resume`) call
fails when the orchestrator does not invoke the retry helper.
"""

import json

import pytest

from app.database import get_db
from app.main import app as _app
from app.models.task import Task
from app.scheduler.task_queue import TaskQueueScheduler
from app.task_states import TaskStatus
from tests.workflow.conftest import wait_for_task
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow


@pytest.fixture
async def with_queue(monkeypatch, override_async_session, mock_browser_wf):
    """Start a real TaskQueueScheduler so wake-via-/messages routes
    through `_execute_woken_and_notify` → `execute_woken_task`.
    """
    scheduler = TaskQueueScheduler()
    monkeypatch.setattr('app.routers.tasks.task_queue_scheduler', scheduler)
    monkeypatch.setattr(
        'app.routers.tasks_conversation.task_queue_scheduler', scheduler
    )
    await scheduler.start()
    yield scheduler
    await scheduler.stop()


async def _create_store(client, name='Retry Test Store'):
    r = await client.post('/api/stores', json={'name': name})
    assert r.status_code == 200
    return r.json()['id']


async def _set_task_state(
    task_id: str,
    *,
    status: TaskStatus,
    session_id: str | None = None,
    wait_condition: dict | None = None,
):
    """Drive the task row directly into a state the orchestrator
    test wants — saves us from spinning a real session that we then
    have to interrupt.
    """
    db_gen = _app.dependency_overrides[get_db]()
    db = await db_gen.__anext__()
    try:
        task = await db.get(Task, task_id)
        task.status = status
        if session_id is not None:
            task.session_id = session_id
        if wait_condition is not None:
            task.wait_condition = json.dumps(wait_condition)
        await db.commit()
    finally:
        await db_gen.aclose()


# ── execute_woken_task: the path that had the bug ──────


class TestWokenTaskResumeRetry:
    """Wake a WAITING task whose first session simulates
    ``claude --resume <stale_id>`` rejection. The orchestrator
    must call ``retry_without_resume`` and the task must end
    COMPLETED.
    """

    async def _seed_waiting_task(
        self, client, install_fake_agent, scenario: FakeAgentScenario
    ) -> str:
        store_id = await _create_store(client)
        # Create + complete a task so it has a session_id we can later
        # mark stale, then drop it back to WAITING.
        r = await client.post(
            '/api/tasks',
            json={
                'title': 'Woken retry task',
                'plan_mode': False,
                'store_id': store_id,
            },
        )
        task_id = r.json()['id']
        await wait_for_task(client, task_id)
        # Pin the resume-failure scenario for THIS task; defaults
        # set on the agent earlier could leak into the retry
        # session (we want it to succeed cleanly).
        install_fake_agent.scenarios[task_id] = scenario
        await _set_task_state(
            task_id,
            status=TaskStatus.WAITING,
            session_id='stale-claude-session-id',
            wait_condition={
                'reason': 'test wait',
                'check_strategy': 'manual',
            },
        )
        return task_id

    async def test_woken_task_retries_and_completes(
        self, admin_client, install_fake_agent, with_queue
    ):
        scenario = FakeAgentScenario(
            simulate_resume_failure_first=True,
            result='Recovered after retry',
        )
        task_id = await self._seed_waiting_task(
            admin_client, install_fake_agent, scenario
        )

        r = await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={'content': 'wake up'},
        )
        assert r.status_code == 200
        assert r.json()['woken'] is True

        data = await wait_for_task(admin_client, task_id)
        assert data['status'] == 'completed'
        assert data['result'] == 'Recovered after retry'

        # Wiring assertion: the orchestrator MUST have called
        # retry_without_resume once. Without the fix to
        # `execute_woken_task`, this stays at zero and the task
        # ends FAILED instead.
        retry_calls = install_fake_agent.get_calls(
            task_id=task_id, action='retry_without_resume'
        )
        assert len(retry_calls) == 1, (
            'execute_woken_task must call retry_without_resume on '
            'a stale --resume rejection'
        )

        # Second `run` call (post-retry) is implied by retry helper
        # spawning a fresh session — but we DO assert the manager
        # was driven through the retry slot (not just a stop+restart).
        run_calls = install_fake_agent.get_calls(task_id=task_id, action='run')
        # 1 from initial PENDING→COMPLETED setup, 1 from the wake
        # that triggered the resume-failure simulation. The retry
        # session is recorded as `retry_without_resume`, not `run`.
        assert len(run_calls) == 2

    async def test_woken_task_clears_stale_state_before_retry(
        self, admin_client, install_fake_agent, with_queue
    ):
        """The orchestrator clears task.session_id/result/error
        before the retry session starts, so a stale prior result
        can't misclassify the retry as success.
        """
        # Pre-seed task.result so the test detects the clear.
        scenario = FakeAgentScenario(
            simulate_resume_failure_first=True,
            result='Fresh after retry',
        )
        task_id = await self._seed_waiting_task(
            admin_client, install_fake_agent, scenario
        )
        # Drop a stale prior result onto the row.
        db_gen = _app.dependency_overrides[get_db]()
        db = await db_gen.__anext__()
        try:
            task = await db.get(Task, task_id)
            task.result = 'STALE PRIOR RESULT — must not survive retry'
            task.error = 'STALE ERROR'
            await db.commit()
        finally:
            await db_gen.aclose()

        r = await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={'content': 'wake up'},
        )
        assert r.status_code == 200

        data = await wait_for_task(admin_client, task_id)
        # If the orchestrator did NOT clear stale state, the
        # finalizer would either keep "STALE PRIOR RESULT" or
        # transition to FAILED on the stale error — both broken.
        assert data['status'] == 'completed'
        assert data['result'] == 'Fresh after retry'
        assert data['error'] is None

    async def test_woken_task_no_retry_when_no_resume_id(
        self, admin_client, install_fake_agent, with_queue
    ):
        """Sanity check: the retry path is gated on
        ``resume_session_id`` being set. A task with no prior
        session_id never triggers retry even when other parts of
        the failure pattern look similar.
        """
        store_id = await _create_store(admin_client)
        # Direct WAITING insert with NO session_id.  The wake will
        # call run(resume=True) but the FakeAgent will set
        # resume_session_id=None because task.session_id is null.
        # `_is_resume_failure` should return False.
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Woken without prior session',
                'plan_mode': False,
                'store_id': store_id,
            },
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id)
        await _set_task_state(
            task_id,
            status=TaskStatus.WAITING,
            session_id=None,
            wait_condition={
                'reason': 'test wait',
                'check_strategy': 'manual',
            },
        )
        install_fake_agent.scenarios[task_id] = FakeAgentScenario(
            simulate_resume_failure_first=True,
            result='Should still complete',
        )

        r = await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={'content': 'wake'},
        )
        assert r.status_code == 200

        # Without resume_session_id the detector returns False, so
        # the simulated rc=1 exit goes through the regular FAILED
        # path (no retry attempted).
        data = await wait_for_task(admin_client, task_id, target='failed')
        assert data['status'] == 'failed'
        retry_calls = install_fake_agent.get_calls(
            task_id=task_id, action='retry_without_resume'
        )
        assert retry_calls == []


# ── finalize_followup_session: chat on a finished task ──


class TestFollowupResumeRetry:
    """Send a follow-up to a COMPLETED task whose first follow-up
    session simulates resume rejection. The retry must complete the
    task back to COMPLETED.
    """

    async def test_followup_on_completed_task_retries(
        self, admin_client, install_fake_agent, with_queue
    ):
        store_id = await _create_store(admin_client)
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Followup retry',
                'plan_mode': False,
                'store_id': store_id,
            },
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id)

        # Set a non-null session_id on the completed row so the
        # follow-up triggers resume=True in agent_manager.run().
        await _set_task_state(
            task_id,
            status=TaskStatus.COMPLETED,
            session_id='stale-followup-sid',
        )
        install_fake_agent.scenarios[task_id] = FakeAgentScenario(
            simulate_resume_failure_first=True,
            result='Followup recovered',
        )

        r = await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={'content': 'do more work'},
        )
        assert r.status_code == 200

        data = await wait_for_task(admin_client, task_id)
        assert data['status'] == 'completed'
        assert data['result'] == 'Followup recovered'
        retry_calls = install_fake_agent.get_calls(
            task_id=task_id, action='retry_without_resume'
        )
        assert len(retry_calls) == 1
