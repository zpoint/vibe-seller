"""The stall reaper's authority split, as a user journey.

The flat 3-min silence rule force-failed live agents whose model was
merely slow to first token (observed with a large tool_result
pending: 3–4 min of healthy silence, 3 incidents in one day). The
reaper now defers to the CLI's own stream-idle watchdog bound (armed
in every spawn env) while the agent process is alive and
input-capable, and keeps the fast rule only when nothing can
progress any more.

These tests drive the real API → task_runner → FakeAgent pipeline,
then backdate ``updated_at`` (the fakeness stands in for a starved
stream heartbeat — the timestamp clock without the death verdict)
and invoke the reaper directly.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.models.task import Task
import app.scheduler.stall_reaper as reaper_mod
from app.scheduler.stall_reaper import reap_stalled_running_tasks
from app.task_states import TaskStatus
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow


async def _create_store(client, name='Reaper Store'):
    r = await client.post('/api/stores', json={'name': name})
    return r.json()['id']


async def _backdate(maker, task_id: str, minutes: float):
    """Age ``updated_at`` — simulates the heartbeat having been
    starved for that long while the agent itself is healthy."""
    stale = (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()
    async with maker() as db:
        task = await db.get(Task, task_id)
        task.updated_at = stale
        await db.commit()


async def _get_task(maker, task_id: str) -> Task:
    async with maker() as db:
        return await db.get(Task, task_id)


class TestSlowFirstTokenNotReaped:
    async def test_alive_inflight_agent_survives_stale_clock(
        self, admin_client, install_fake_agent, override_async_session
    ):
        """Slow-model scenario: agent in flight (FakeAgent gated mid-
        work), no events for 10 min of clock — far past the old 3-min
        rule, inside the CLI's stream-silence bound (13 min with the
        injected watchdog defaults). Not reaped."""
        store_id = await _create_store(admin_client)
        gate = asyncio.Event()
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='composed report after long silence',
            gate=gate,
        )
        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Slow compose', 'store_id': store_id},
        )
        task_id = r.json()['id']

        # Wait until the agent is genuinely in flight.
        for _ in range(250):
            if install_fake_agent.is_running(task_id):
                break
            await asyncio.sleep(0.02)
        assert install_fake_agent.is_running(task_id)
        assert install_fake_agent.is_process_alive(task_id)

        await _backdate(override_async_session, task_id, minutes=10)
        await reap_stalled_running_tasks()

        task = await _get_task(override_async_session, task_id)
        assert task.status == TaskStatus.RUNNING
        assert task.error is None

        # The model finishes (first token finally arrives); the task
        # completes normally — no force-fail scarred it.
        gate.set()
        for _ in range(250):
            task = await _get_task(override_async_session, task_id)
            if task.status == TaskStatus.COMPLETED:
                break
            await asyncio.sleep(0.02)
        assert task.status == TaskStatus.COMPLETED
        assert task.error is None

    async def test_past_request_bound_alive_agent_is_reaped(
        self,
        admin_client,
        install_fake_agent,
        monkeypatch,
        override_async_session,
    ):
        """Dead-stream scenario: silence has outlasted the CLI's own
        request timeout (bound shrunk here to keep the test fast)
        with the process alive — the reaper is the backstop and
        force-fails within the bound."""
        store_id = await _create_store(admin_client, 'Reaper Store 2')
        gate = asyncio.Event()
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='never arrives', gate=gate
        )
        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Dead stream', 'store_id': store_id},
        )
        task_id = r.json()['id']
        for _ in range(250):
            if install_fake_agent.is_running(task_id):
                break
            await asyncio.sleep(0.02)
        assert install_fake_agent.is_process_alive(task_id)

        await _backdate(override_async_session, task_id, minutes=10)
        monkeypatch.setattr(
            reaper_mod,
            '_stream_silence_bound',
            lambda task: timedelta(minutes=5),
        )
        await reap_stalled_running_tasks()

        task = await _get_task(override_async_session, task_id)
        assert task.status == TaskStatus.FAILED
        assert task.error_category == 'agent_stream_stalled'

        gate.set()  # release the fake's coroutine so the test cleans up

    async def test_dead_process_reaped_promptly(
        self, admin_client, install_fake_agent, override_async_session
    ):
        """Dead-process scenario: the session's process is gone (a
        crash/finalizer loss) — staleness is authoritative again and
        the flat rule applies without waiting for any request
        timeout."""
        store_id = await _create_store(admin_client, 'Reaper Store 3')
        gate = asyncio.Event()
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='whatever', gate=gate
        )
        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Crashed agent', 'store_id': store_id},
        )
        task_id = r.json()['id']
        for _ in range(250):
            if install_fake_agent.is_running(task_id):
                break
            await asyncio.sleep(0.02)

        # Simulate the dead process: the session reports exited while
        # the task row stays RUNNING (finalizer lost — the state the
        # reaper exists for).
        session = install_fake_agent.get_session(task_id)
        session._proc.returncode = 0
        session.running = False

        await _backdate(override_async_session, task_id, minutes=4)
        await reap_stalled_running_tasks()

        task = await _get_task(override_async_session, task_id)
        assert task.status == TaskStatus.FAILED
        assert task.error_category == 'agent_stream_stalled'

        gate.set()
