"""Workflow tests for auto mode (plan_mode=False default).

Verifies: auto mode skips DESIGNING/PLANNED states, uses mode='auto',
completes/fails correctly, stop/retry/follow-up/todos/woken
all work without a plan.
"""

import pytest

from app.models.task import Task
from app.task_states import TaskStatus
from tests.workflow.conftest import wait_for_task
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow


async def _create_store(client, name='Auto Test Store'):
    """Helper: create a store and return its id."""
    r = await client.post('/api/stores', json={'name': name})
    assert r.status_code == 200
    return r.json()['id']


# ── Lifecycle ────────────────────────────────────────


class TestAutoModeLifecycle:
    async def test_auto_mode_skips_designing(
        self, admin_client, install_fake_agent
    ):
        """Default task goes PENDING → RUNNING → COMPLETED."""
        store_id = await _create_store(admin_client)
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Auto test',
                'plan_mode': False,
                'store_id': store_id,
            },
        )
        task_id = r.json()['id']
        data = await wait_for_task(admin_client, task_id)
        assert data['status'] == 'completed'
        assert data['plan'] is None
        assert data['result'] is not None

    async def test_auto_mode_agent_called_with_auto(
        self, admin_client, install_fake_agent
    ):
        """Agent.run() called with mode='auto' for default tasks."""
        store_id = await _create_store(admin_client)
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Mode check',
                'plan_mode': False,
                'store_id': store_id,
            },
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id)
        calls = install_fake_agent.get_calls(task_id=task_id, action='run')
        assert calls[0].mode == 'auto'

    async def test_auto_mode_error_result_fails(
        self, admin_client, install_fake_agent
    ):
        """Auto mode task with error result → FAILED."""
        store_id = await _create_store(admin_client)
        install_fake_agent.default_scenario = FakeAgentScenario(
            error_result='API Error: 500',
        )
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Auto fail',
                'plan_mode': False,
                'store_id': store_id,
            },
        )
        data = await wait_for_task(
            admin_client, r.json()['id'], target='failed'
        )
        assert data['status'] == 'failed'
        assert '500' in data['error']

    async def test_auto_mode_execution_failure(
        self, admin_client, install_fake_agent
    ):
        """Auto mode agent exits without result → FAILED."""
        store_id = await _create_store(admin_client)
        install_fake_agent.default_scenario = FakeAgentScenario(
            should_fail=True,
            fail_at_phase='execute',
        )
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Auto no result',
                'plan_mode': False,
                'store_id': store_id,
            },
        )
        data = await wait_for_task(
            admin_client, r.json()['id'], target='failed'
        )
        assert data['status'] == 'failed'


# ── Stop / Retry ─────────────────────────────────────


class TestAutoModeStopRetry:
    async def test_auto_mode_stop_running(
        self, admin_client, install_fake_agent
    ):
        """Stop auto-mode task during RUNNING → FAILED."""
        store_id = await _create_store(admin_client)
        install_fake_agent.default_scenario = FakeAgentScenario(
            execute_delay=2.0,
        )
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Auto stop',
                'plan_mode': False,
                'store_id': store_id,
            },
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id, target='running')
        await admin_client.post(f'/api/tasks/{task_id}/agent/stop')
        data = await wait_for_task(admin_client, task_id, target='failed')
        assert data['status'] == 'failed'

    async def test_auto_mode_retry(self, admin_client, install_fake_agent):
        """Retry auto-mode failed task → completes in auto mode."""
        store_id = await _create_store(admin_client)
        install_fake_agent.default_scenario = FakeAgentScenario(
            should_fail=True,
            fail_at_phase='execute',
        )
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Auto retry',
                'plan_mode': False,
                'store_id': store_id,
            },
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id, target='failed')

        install_fake_agent.default_scenario = FakeAgentScenario(
            result='Retried ok',
        )
        await admin_client.post(f'/api/tasks/{task_id}/retry')
        data = await wait_for_task(admin_client, task_id)
        assert data['status'] == 'completed'
        assert data['plan'] is None


# ── Follow-up ────────────────────────────────────────


class TestAutoModeFollowUp:
    async def test_auto_mode_follow_up_uses_auto(
        self, admin_client, install_fake_agent
    ):
        """Follow-up on completed auto task → starts auto session."""
        store_id = await _create_store(admin_client)
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Auto followup',
                'plan_mode': False,
                'store_id': store_id,
            },
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id)

        install_fake_agent.calls.clear()
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='Follow-up done',
        )
        await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={'content': 'Do more'},
        )
        # Wait for new session to complete
        await wait_for_task(admin_client, task_id)

        run_calls = install_fake_agent.get_calls(task_id=task_id, action='run')
        assert run_calls[-1].mode == 'auto'

    async def test_auto_mode_follow_up_clears_stale_data(
        self, admin_client, install_fake_agent
    ):
        """Follow-up on completed auto task clears old result."""
        store_id = await _create_store(admin_client)
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Stale test',
                'plan_mode': False,
                'store_id': store_id,
            },
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id)
        old = (await admin_client.get(f'/api/tasks/{task_id}')).json()
        assert old['result'] is not None

        # Send follow-up
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='New result',
        )
        await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={'content': 'Do more'},
        )
        data = await wait_for_task(admin_client, task_id)
        assert data['result'] == 'New result'


# ── Todos / Waiting ──────────────────────────────────


class TestAutoModeTodos:
    async def test_auto_mode_incomplete_todos_no_result_waiting(
        self, admin_client, install_fake_agent
    ):
        """Auto mode with incomplete todos and no result → WAITING."""
        store_id = await _create_store(admin_client)
        install_fake_agent.default_scenario = FakeAgentScenario(
            todos=[
                {'content': 'Done', 'status': 'completed'},
                {'content': 'Not done', 'status': 'in_progress'},
            ],
            result=None,
        )
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Auto todos',
                'plan_mode': False,
                'store_id': store_id,
            },
        )
        data = await wait_for_task(
            admin_client, r.json()['id'], target='waiting'
        )
        assert data['status'] == 'waiting'

    async def test_auto_mode_woken_task_queued(
        self, admin_client, install_fake_agent
    ):
        """Woken auto-mode task transitions to QUEUED via message."""
        store_id = await _create_store(admin_client)
        install_fake_agent.default_scenario = FakeAgentScenario(
            todos=[
                {'content': 'Wait', 'status': 'in_progress'},
            ],
            result=None,
        )
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Wake auto',
                'plan_mode': False,
                'store_id': store_id,
            },
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id, target='waiting')

        # Wake the task — goes to QUEUED
        r2 = await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={'content': 'Go'},
        )
        assert r2.status_code == 200
        assert r2.json().get('woken') is True


# ── Agent-annotated error + user-stop terminal paths ─────────
#
# The MCP tools `vibe_seller_set_task_error` and
# `vibe_seller_set_task_result` are annotation-only — they write
# `task.error` / `task.result` but never transition status.
# `auto_run_task`'s cleanup pipeline (§7) owns all status
# transitions for agent-driven endings, so post-task knowledge
# commit and metadata sync always run.
#
# The one remaining mid-session short-circuit path is user-initiated
# stop (`POST /agent/stop`), which flips status to FAILED directly.
# The wait loop's 10-tick poll catches that so we don't wait the
# full session timeout for a process the user explicitly killed.


class TestAgentAnnotatedError:
    async def test_set_task_error_transitions_to_failed_via_cleanup(
        self,
        admin_client,
        install_fake_agent,
        override_async_session,
    ):
        """Agent writes task.error via MCP; pipeline transitions to
        FAILED during §7 cleanup after the agent session ends.

        Status stays RUNNING until the session ends — this is what
        lets §5b (knowledge commit) and §5c (metadata sync) run,
        which they wouldn't if the MCP call flipped status
        mid-session (the old vibe_seller_fail_task behaviour).
        """
        store_id = await _create_store(admin_client)
        # Keep the fake agent running long enough to write the
        # annotation before its session ends. When the session
        # exits, §7 sees task.error set and transitions to FAILED.
        install_fake_agent.default_scenario = FakeAgentScenario(
            execute_delay=2.0,
        )
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Agent-annotated error test',
                'plan_mode': False,
                'store_id': store_id,
            },
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id, target='running')

        # Simulate the agent calling set_task_error via MCP: write
        # task.error but leave status RUNNING. The route handler
        # does this exact thing (app/routers/tasks.py:set_task_error).
        async with override_async_session() as db:
            task = await db.get(Task, task_id)
            task.error = 'browser will not start'
            task.error_category = 'agent_reported'
            await db.commit()

        # Status stays RUNNING while the agent session is alive —
        # the MCP annotation does NOT short-circuit the pipeline.
        r_poll = await admin_client.get(f'/api/tasks/{task_id}')
        assert r_poll.json()['status'] == 'running'

        # Pipeline lands on FAILED only after the fake-agent
        # session ends naturally, *not* short-circuited by the
        # annotation itself.
        data = await wait_for_task(
            admin_client,
            task_id,
            target='failed',
            timeout=20.0,
        )
        assert data['status'] == 'failed'
        assert data['error'] == 'browser will not start'
        assert data['error_category'] == 'agent_reported'


class TestUserStopTerminalDetection:
    async def test_user_stop_detected_by_poll_loop(
        self,
        admin_client,
        install_fake_agent,
        override_async_session,
    ):
        """Pipeline exits promptly when the user flips status to
        FAILED directly (e.g. POST /agent/stop), instead of waiting
        the full session timeout.

        This guards the wait-loop's 10-tick terminal-state poll,
        which is now the *only* mid-session short-circuit after the
        MCP tools were refactored to annotation-only.
        """
        store_id = await _create_store(admin_client)
        install_fake_agent.default_scenario = FakeAgentScenario(
            execute_delay=30.0,  # agent stays "running" for 30s
        )
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'User stop test',
                'plan_mode': False,
                'store_id': store_id,
            },
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id, target='running')

        # Simulate user-initiated stop: status flipped directly to
        # FAILED. This mirrors what /agent/stop does at routers/tasks.py.
        async with override_async_session() as db:
            task = await db.get(Task, task_id)
            task.status = TaskStatus.FAILED
            task.error = 'Stopped by user'
            task.error_category = 'stopped_by_user'
            await db.commit()

        data = await wait_for_task(
            admin_client,
            task_id,
            target='failed',
            timeout=20.0,
        )
        assert data['status'] == 'failed'
        assert data['error'] == 'Stopped by user'
