"""Turn lifecycle under the process-per-turn model.

A CLI process may stay alive after its turn's result (running
subagents, awaiting notifications). Two contracts:

- a follow-up POSTed during that window is INJECTED into the live
  process (send_message), not spawned as a new session — and the task
  still finalizes normally when the process ends;
- a follow-up whose stdin write FAILS (raced the turn terminator)
  falls through to a fresh spawn instead of being silently dropped.
"""

import asyncio

import pytest

from app.models.task import Task
from tests.workflow.conftest import wait_for_task
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow


async def _create_store(client, name='Turn Lifecycle Store'):
    r = await client.post('/api/stores', json={'name': name})
    return r.json()['id']


class TestPostResultWindow:
    async def test_followup_during_post_result_window_injects(
        self, admin_client, install_fake_agent
    ):
        """While the process lives past its result, a follow-up routes
        via send_message and the task still completes at exit."""
        store_id = await _create_store(admin_client)
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='turn one done',
            post_result_activity=[
                ('assistant', 'subagent still verifying in background'),
            ],
            exit_delay=1.0,
        )
        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Post-result window', 'store_id': store_id},
        )
        task_id = r.json()['id']

        # Wait until the result card exists but the session is alive
        # (the exit_delay window).
        for _ in range(200):
            msgs = (
                await admin_client.get(f'/api/tasks/{task_id}/messages')
            ).json()
            if any(m['role'] == 'result' for m in msgs):
                break
            await asyncio.sleep(0.02)
        assert install_fake_agent.is_running(task_id)

        r2 = await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={'content': 'also do the other thing'},
        )
        assert r2.status_code == 200 and r2.json()['ok'] is True

        sends = install_fake_agent.get_calls(
            task_id=task_id, action='send_message'
        )
        assert len(sends) == 1
        assert sends[0].message == 'also do the other thing'

        # The process's exit still finalizes the task normally.
        await wait_for_task(admin_client, task_id)

    async def test_failed_delivery_falls_back_to_spawn(
        self, admin_client, install_fake_agent
    ):
        """send_message returning False (write raced the turn
        terminator) must fall through to a fresh spawn — the message
        is never silently dropped."""
        store_id = await _create_store(admin_client)
        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Delivery race', 'store_id': store_id},
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id)

        # Simulate the race: the manager says "running" but the write
        # lands in a closed pipe.
        install_fake_agent._running[task_id] = True
        real_send = install_fake_agent.send_message

        async def _failing_send(tid, message):
            await real_send(tid, message)  # record the attempt
            # A failed write means the turn terminator fired between
            # the router's is_running check and the stdin write — the
            # session is no longer input-capable.
            install_fake_agent._running[tid] = False
            return False

        install_fake_agent.send_message = _failing_send
        try:
            runs_before = len(
                install_fake_agent.get_calls(task_id=task_id, action='run')
            )
            r2 = await admin_client.post(
                f'/api/tasks/{task_id}/messages',
                json={'content': 'do not lose me'},
            )
            assert r2.status_code == 200
        finally:
            install_fake_agent.send_message = real_send
            install_fake_agent._running[task_id] = False

        # Fell through to a fresh spawn (resume/fresh-session path).
        for _ in range(200):
            runs_after = len(
                install_fake_agent.get_calls(task_id=task_id, action='run')
            )
            if runs_after > runs_before:
                break
            await asyncio.sleep(0.02)
        assert runs_after > runs_before

        # The user message was persisted exactly once.
        msgs = (await admin_client.get(f'/api/tasks/{task_id}/messages')).json()
        user_msgs = [
            m
            for m in msgs
            if m['role'] == 'user' and m['content'] == 'do not lose me'
        ]
        assert len(user_msgs) == 1
        await wait_for_task(admin_client, task_id)


class TestTurnScopedVerdict:
    """A delivered follow-up opens a new turn — the prior turn's
    task-level verdict (result/error) must not survive it. Without the
    clear, ``_save_result``'s preserve-existing rule refuses the new
    turn's streamed result and the UI shows a stale verdict next to
    the new turn's answer."""

    async def test_injected_followup_clears_prior_result_and_error(
        self, admin_client, install_fake_agent, override_async_session
    ):
        store_id = await _create_store(admin_client, 'Verdict Scope Store')
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='turn one verdict',
            exit_delay=2.0,
        )
        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Turn-scoped verdict', 'store_id': store_id},
        )
        task_id = r.json()['id']

        # Wait for turn 1's verdict to land while the process lives on.
        for _ in range(200):
            data = (await admin_client.get(f'/api/tasks/{task_id}')).json()
            if data.get('result'):
                break
            await asyncio.sleep(0.02)
        assert data['result'] == 'turn one verdict'
        assert install_fake_agent.is_running(task_id)

        # A prior error too (e.g. turn 1 called set_task_error).
        async with override_async_session() as db:
            task = await db.get(Task, task_id)
            task.error = 'turn one error'
            task.error_category = 'agent_reported'
            await db.commit()

        r2 = await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={'content': 'please continue with the next part'},
        )
        assert r2.status_code == 200 and r2.json()['ok'] is True

        data = (await admin_client.get(f'/api/tasks/{task_id}')).json()
        assert not data.get('result')
        assert not data.get('error')

        await wait_for_task(admin_client, task_id)
