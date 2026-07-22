"""Idle-browser sweep against real task lifecycles.

The unit tier pins the guard matrix with mocked bindings; this tier
drives ``sweep_idle_browsers`` against REAL tasks in the DB (created
through the API, run by FakeAgent) so the ``_live_task_bindings``
query and every user-facing case are exercised:

- short-run task: completes → its store's browser is swept;
- long-run task: still RUNNING (gate held) → browser held;
- same store, multiple tasks: one completed + one running → held
  until the last one finishes;
- different stores: swept/held independently in one pass;
- WAITING task: does NOT hold the browser (lazy restart on wake).

Time is mocked, not slept: fake proxies report an ancient
``idle_seconds`` and the window comes from ``VIBE_BROWSER_IDLE_S``.
"""

import asyncio
from unittest import mock

import pytest

from app.browser import aux_browser
from app.browser.idle_sweep import sweep_idle_browsers
from app.browser.manager import browser_manager
from app.env_options import Options
from app.models.task import Task
from tests.workflow.conftest import wait_for_task
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow


class _FakeProxy:
    def __init__(self, clients=0, idle=1e9):
        self._clients = clients
        self._idle = idle

    def has_active_clients(self):
        return self._clients > 0

    def idle_seconds(self):
        return self._idle


class _FakeBackend:
    def __init__(self):
        self._proxy = _FakeProxy()


@pytest.fixture
def browsers(monkeypatch):
    """Fake browser registries + captured stops; real DB bindings."""
    monkeypatch.setenv(Options.BROWSER_IDLE_S.env_var, '300')
    stopped = []

    async def _stop_session(store, db):
        stopped.append(store.id)
        browser_manager._active_sessions.pop(store.id, None)

    monkeypatch.setattr(browser_manager, 'stop_session', _stop_session)
    monkeypatch.setattr(browser_manager, '_active_sessions', {})
    monkeypatch.setattr(browser_manager, '_backends', {})
    monkeypatch.setattr(aux_browser, '_backends', {})

    def _register(store_id):
        browser_manager._active_sessions[store_id] = mock.Mock()
        browser_manager._backends[store_id] = _FakeBackend()

    return _register, stopped


async def _create_store(client, name):
    r = await client.post('/api/stores', json={'name': name})
    return r.json()['id']


async def _wait_running(client, task_id):
    for _ in range(200):
        data = (await client.get(f'/api/tasks/{task_id}')).json()
        if data['status'] == 'running':
            return
        await asyncio.sleep(0.02)
    raise AssertionError('task never reached running')


class TestSweepWithRealTasks:
    async def test_case_matrix_in_one_pass(
        self, admin_client, install_fake_agent, browsers
    ):
        register, stopped = browsers

        # Store A — short-run task, already completed.
        store_a = await _create_store(admin_client, 'Sweep Store A')
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='quick job done'
        )
        r = await admin_client.post(
            '/api/tasks', json={'title': 'short run', 'store_id': store_a}
        )
        await wait_for_task(admin_client, r.json()['id'])

        # Store B — long-run task, still RUNNING behind a gate.
        store_b = await _create_store(admin_client, 'Sweep Store B')
        gate = asyncio.Event()
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='long job done', gate=gate
        )
        rb = await admin_client.post(
            '/api/tasks', json={'title': 'long run', 'store_id': store_b}
        )
        long_task = rb.json()['id']
        await _wait_running(admin_client, long_task)

        # Store B also has a COMPLETED task (same store, multiple
        # tasks) — the running one must still hold the browser.
        install_fake_agent.scenarios.clear()

        register(store_a)
        register(store_b)

        assert await sweep_idle_browsers() == 1
        assert stopped == [store_a]

        # Long task finishes → B is swept on the next pass.
        gate.set()
        await wait_for_task(admin_client, long_task)
        assert await sweep_idle_browsers() == 1
        assert stopped == [store_a, store_b]

    async def test_waiting_task_does_not_hold_browser(
        self, admin_client, install_fake_agent, browsers, override_async_session
    ):
        register, stopped = browsers
        store_id = await _create_store(admin_client, 'Sweep Waiting Store')
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='now waiting'
        )
        r = await admin_client.post(
            '/api/tasks', json={'title': 'parks itself', 'store_id': store_id}
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id)

        # Park it WAITING (the wait-condition flow's terminal state).
        async with override_async_session() as db:
            t = await db.get(Task, task_id)
            t.status = 'waiting'
            await db.commit()

        register(store_id)
        assert await sweep_idle_browsers() == 1
        assert stopped == [store_id]

    async def test_pending_queued_tasks_hold_browser(
        self, admin_client, install_fake_agent, browsers, override_async_session
    ):
        # A task that hasn't STARTED yet (queued for the agent
        # semaphore — the #90 scenario) must still hold the browser it
        # is about to use.
        register, stopped = browsers
        store_id = await _create_store(admin_client, 'Sweep Queued Store')
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='eventually'
        )
        r = await admin_client.post(
            '/api/tasks', json={'title': 'queued', 'store_id': store_id}
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id)
        async with override_async_session() as db:
            t = await db.get(Task, task_id)
            t.status = 'queued'
            await db.commit()

        register(store_id)
        assert await sweep_idle_browsers() == 0
        assert stopped == []
