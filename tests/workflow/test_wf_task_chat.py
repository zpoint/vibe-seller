"""Workflow tests for task chat messages, questions, and profile switching."""

import json

import pytest

from app.database import get_db
from app.main import app as _app
from app.models.task import Task
from app.task_states import TaskStatus
from tests.workflow.conftest import wait_for_task

pytestmark = pytest.mark.workflow


async def _create_store(client, name='Chat Test Store'):
    """Helper: create a store and return its id."""
    r = await client.post('/api/stores', json={'name': name})
    assert r.status_code == 200
    return r.json()['id']


class TestTaskMessages:
    async def _create_and_wait(self, client, title='Chat task'):
        """Create a task and wait for the auto-run pipeline to finish."""
        store_id = await _create_store(client)
        r = await client.post(
            '/api/tasks',
            json={
                'title': title,
                'plan_mode': False,
                'store_id': store_id,
            },
        )
        assert r.status_code == 200
        task_id = r.json()['id']
        await wait_for_task(client, task_id)
        return task_id

    async def test_send_message_to_running_agent(
        self, admin_client, install_fake_agent
    ):
        """POST message when agent is running -> send_message call."""
        task_id = await self._create_and_wait(admin_client)

        # Manually set agent as running so send_message path is taken
        install_fake_agent._running[task_id] = True

        r = await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={'content': 'hello agent'},
        )
        assert r.status_code == 200
        assert r.json()['ok'] is True

        # Verify send_message was called
        msgs = install_fake_agent.get_calls(
            task_id=task_id, action='send_message'
        )
        assert len(msgs) == 1
        assert msgs[0].message == 'hello agent'

        # Clean up
        install_fake_agent._running[task_id] = False

    async def test_send_message_starts_agent(
        self, admin_client, install_fake_agent
    ):
        """Agent not running -> message starts new agent session."""
        task_id = await self._create_and_wait(admin_client)

        # Agent should not be running after pipeline completes
        assert not install_fake_agent.is_running(task_id)

        # Send a message
        r = await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={'content': 'follow up'},
        )
        assert r.status_code == 200

        # Agent should have been started
        run_calls = install_fake_agent.get_calls(task_id=task_id, action='run')
        # Should have at least 2 run calls: plan_then_execute + chat
        assert len(run_calls) >= 2

    async def test_get_message_history(self, admin_client, install_fake_agent):
        """GET /api/tasks/{id}/messages returns ordered message list."""
        task_id = await self._create_and_wait(admin_client)

        # Send two messages
        install_fake_agent._running[task_id] = True
        await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={'content': 'msg 1'},
        )
        await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={'content': 'msg 2'},
        )
        install_fake_agent._running[task_id] = False

        r = await admin_client.get(f'/api/tasks/{task_id}/messages')
        assert r.status_code == 200
        messages = r.json()
        user_msgs = [m for m in messages if m['role'] == 'user']
        assert len(user_msgs) == 2
        assert user_msgs[0]['content'] == 'msg 1'
        assert user_msgs[1]['content'] == 'msg 2'

    async def test_answer_question(self, admin_client, install_fake_agent):
        """POST /api/tasks/{id}/questions/answer -> forwarded to agent."""
        task_id = await self._create_and_wait(admin_client)
        install_fake_agent._running[task_id] = True

        r = await admin_client.post(
            f'/api/tasks/{task_id}/questions/answer',
            json={
                'request_id': 'q-123',
                'answers': {'q1': 'yes'},
            },
        )
        assert r.status_code == 200
        install_fake_agent._running[task_id] = False

    async def test_profile_switch_restarts_agent(
        self, admin_client, install_fake_agent
    ):
        """Send message with new profile_id -> old agent stopped, new started."""
        # Create profile
        await admin_client.post(
            '/api/profiles',
            json={
                'id': 'switch-profile',
                'name': 'Switch',
                'env': {},
            },
        )

        task_id = await self._create_and_wait(admin_client)

        # Now send message with different profile
        install_fake_agent._running[task_id] = True
        r = await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={
                'content': 'with new profile',
                'profile_id': 'switch-profile',
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body['profile_switched'] is True
        assert body['profile_id'] == 'switch-profile'

    async def test_message_wakes_waiting_task(
        self, admin_client, install_fake_agent
    ):
        """POST message to waiting task wakes it via unified chat."""
        task_id = await self._create_and_wait(admin_client)

        # Manually set task to waiting status with wait_condition
        wait_cond = json.dumps({
            'reason': 'test wait',
            'check_strategy': 'manual',
            'waiting_since': '2026-01-01T00:00:00Z',
        })
        r = await admin_client.get(f'/api/tasks/{task_id}')
        assert r.status_code == 200

        # Patch status to waiting via DB
        db_gen = _app.dependency_overrides[get_db]()
        db = await db_gen.__anext__()
        try:
            task = await db.get(Task, task_id)
            task.status = 'waiting'
            task.wait_condition = wait_cond
            await db.commit()
        finally:
            await db_gen.aclose()

        # Send message -- should wake the task
        r = await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={'content': 'wake up now'},
        )
        assert r.status_code == 200
        body = r.json()
        assert body['ok'] is True
        assert body['woken'] is True
        assert body['profile_switched'] is False

        # Verify task is now queued
        r = await admin_client.get(f'/api/tasks/{task_id}')
        assert r.status_code == 200
        assert r.json()['status'] == 'queued'

        # Verify message was persisted
        r = await admin_client.get(f'/api/tasks/{task_id}/messages')
        assert r.status_code == 200
        msgs = r.json()
        user_msgs = [m for m in msgs if m['role'] == 'user']
        assert any(m['content'] == 'wake up now' for m in user_msgs)

        # Verify trigger_data was saved in wait_condition
        r = await admin_client.get(f'/api/tasks/{task_id}')
        cond = json.loads(r.json()['wait_condition'])
        assert cond['trigger_data'] == 'wake up now'
        assert cond['woken_by'] == 'user'


class TestFollowUpMessages:
    """Follow-up messages on completed/failed tasks.

    Revert check: removing COMPLETED->DESIGNING transition causes
    500; removing the elif branch causes mode='execute' instead
    of 'plan_then_execute'.
    """

    async def _create_and_wait(self, client, title='Follow-up task'):
        store_id = await _create_store(client)
        r = await client.post(
            '/api/tasks',
            json={
                'title': title,
                'plan_mode': False,
                'store_id': store_id,
            },
        )
        assert r.status_code == 200
        task_id = r.json()['id']
        await wait_for_task(client, task_id)
        return task_id

    async def test_followup_on_completed_task(
        self, admin_client, install_fake_agent
    ):
        """Completed task + message -> 200, mode=plan_then_execute,
        message persisted."""
        task_id = await self._create_and_wait(admin_client)

        r = await admin_client.get(f'/api/tasks/{task_id}')
        assert r.json()['status'] == 'completed'

        runs_before = len(
            install_fake_agent.get_calls(task_id=task_id, action='run')
        )

        r = await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={'content': 'keep going, get all 807 pages'},
        )
        assert r.status_code == 200
        assert r.json()['ok'] is True

        # Verify run call used auto mode (default, not execute)
        all_runs = install_fake_agent.get_calls(task_id=task_id, action='run')
        followup_run = all_runs[runs_before]
        assert followup_run.mode == 'auto'
        assert followup_run.prompt == ('keep going, get all 807 pages')

        # Verify user message persisted
        r = await admin_client.get(f'/api/tasks/{task_id}/messages')
        user_msgs = [m for m in r.json() if m['role'] == 'user']
        assert any(
            m['content'] == 'keep going, get all 807 pages' for m in user_msgs
        )

    async def test_followup_on_failed_task_clears_error(
        self, admin_client, install_fake_agent
    ):
        """Failed task + message -> error/error_category cleared."""
        task_id = await self._create_and_wait(admin_client)

        # Patch to failed with error via DB
        db_gen = _app.dependency_overrides[get_db]()
        db = await db_gen.__anext__()
        try:
            task = await db.get(Task, task_id)
            task.status = TaskStatus.FAILED
            task.error = 'something broke'
            task.error_category = 'agent_error'
            await db.commit()
        finally:
            await db_gen.aclose()

        r = await admin_client.get(f'/api/tasks/{task_id}')
        assert r.json()['status'] == 'failed'
        assert r.json()['error'] == 'something broke'

        r = await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={'content': 'try different approach'},
        )
        assert r.status_code == 200

        # Verify error cleared via DB (API may not expose
        # error_category)
        db_gen = _app.dependency_overrides[get_db]()
        db = await db_gen.__anext__()
        try:
            task = await db.get(Task, task_id)
            assert task.error is None
            assert task.error_category is None
        finally:
            await db_gen.aclose()

    async def test_followup_uses_plan_then_execute_not_execute(
        self, admin_client, install_fake_agent
    ):
        """Follow-up must use plan_then_execute, not execute mode.

        Removing the elif COMPLETED/FAILED branch causes
        fall-through to else which uses mode='execute'.
        """
        task_id = await self._create_and_wait(admin_client)
        runs_before = len(
            install_fake_agent.get_calls(task_id=task_id, action='run')
        )

        await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={'content': 'what did you find?'},
        )

        all_runs = install_fake_agent.get_calls(task_id=task_id, action='run')
        followup_run = all_runs[runs_before]
        # Default tasks use auto mode (not execute) for follow-ups
        assert followup_run.mode == 'auto'

    async def test_followup_regenerates_browser_config(
        self, admin_client, install_fake_agent, mock_browser_wf
    ):
        """Follow-up on a store task must call
        write_browser_config_for_store() so the wrapper has a
        fresh auth token for browser auto-start.

        Without this, a follow-up sent after the token expires
        (e.g. next day) fails to access the browser because the
        wrapper's auto-start curl gets a 401.

        Revert check: removing the write_browser_config call
        from the follow-up branch means config_calls count
        won't increase after the initial auto-run.
        """
        # Create a store so the task is store-bound
        r = await admin_client.post(
            '/api/stores',
            json={'name': 'Browser Test Store'},
        )
        assert r.status_code == 200
        store_id = r.json()['id']

        # Create a task on that store and wait for completion
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'scrape pages',
                'store_id': store_id,
            },
        )
        assert r.status_code == 200
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id)

        # Record how many browser config calls happened so far
        # (auto-run calls it once during auto_run_task)
        calls_before = len(mock_browser_wf.config_calls)
        assert calls_before >= 1  # sanity: auto-run called it

        # Send follow-up
        r = await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={'content': 'get the remaining pages'},
        )
        assert r.status_code == 200

        # Verify browser config was regenerated for follow-up
        calls_after = len(mock_browser_wf.config_calls)
        assert calls_after > calls_before, (
            'write_browser_config_for_store() not called during follow-up'
        )
