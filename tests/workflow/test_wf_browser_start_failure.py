"""A terminal store-browser start failure ends the task (C2 contract).

Driven through the REAL route the wrapper calls
(``POST /api/stores/{id}/browser/start``) with the header the wrapper
sends, against a live app + FakeAgent, so the whole task-layer
contract is exercised:

- each TERMINAL taxonomy code → task FAILED with the typed error and
  the snake-cased category, agent stopped;
- a TRANSIENT code → typed body, task keeps RUNNING (the wrapper's
  retry is its own layer; the server does not fail the task);
- a failure WITHOUT a taxonomy code (Ziniao's path) → exactly today's
  plain 500 detail, task keeps RUNNING — Ziniao is not reclassified;
- the ``X-Vibe-Task`` header only SELECTS this store's own live task:
  another store's task, or a completed one, is left untouched.
"""

import asyncio

import pytest

from app.browser.inject.errors import (
    AccountMismatch,
    ApiNotEnabled,
    ClientNotRunning,
    InjectError,
    LoginRequired,
    PlanRequired,
    StoreNotFound,
    StoreUnavailable,
    UnsupportedKernel,
)
from tests.workflow.conftest import wait_for_task
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow

TERMINAL_CASES = [
    UnsupportedKernel('kernel reports Firefox/100'),
    StoreUnavailable('no device bound'),
    StoreNotFound('missing'),
    PlanRequired('api needs a plan'),
    ApiNotEnabled('api off'),
    AccountMismatch('different account'),
    LoginRequired(),
]


async def _create_store(client, name: str) -> str:
    r = await client.post('/api/stores', json={'name': name})
    assert r.status_code == 200, r.text
    return r.json()['id']


class _FailingManager:
    """Stand-in for BrowserManager whose start raises *exc*.

    The route-level contract is what this tier pins: how an
    ``InjectError`` from the start path reaches the caller and the
    task. That the real manager passes the exception through
    unchanged is pinned in unit tests (launch_guards / engine), and
    the tier-1 e2e runs the real chain end to end.
    """

    def __init__(self, exc: Exception):
        self._exc = exc

    async def start_session(self, store, db):
        raise self._exc

    async def ensure_ziniao_ready(self, store, db, **kwargs):
        return None


@pytest.fixture
def failing_browser(monkeypatch):
    """Point the stores router at a manager that raises on demand."""

    def _install(exc: Exception):
        monkeypatch.setattr(
            'app.routers.stores.browser_manager', _FailingManager(exc)
        )

    return _install


async def _make_running_task(
    client, install_fake_agent, store_id: str
) -> tuple[str, asyncio.Event]:
    """Create a task held RUNNING by a gated FakeAgent scenario."""
    gate = asyncio.Event()
    install_fake_agent.default_scenario = FakeAgentScenario(
        result='done after gate', gate=gate
    )
    r = await client.post(
        '/api/tasks',
        json={'title': 'open the store backend', 'store_id': store_id},
    )
    assert r.status_code == 200, r.text
    task_id = r.json()['id']
    # Wait until the agent is actually running (the wrapper's start
    # call happens mid-run, never before).
    for _ in range(200):
        data = (await client.get(f'/api/tasks/{task_id}')).json()
        if data['status'] == 'running':
            return task_id, gate
        await asyncio.sleep(0.02)
    raise AssertionError('task never reached running')


class TestTerminalStartFailureEndsTask:
    @pytest.mark.parametrize('exc', TERMINAL_CASES, ids=lambda e: e.code)
    async def test_task_failed_with_code_and_category(
        self, admin_client, install_fake_agent, failing_browser, exc
    ):
        store_id = await _create_store(admin_client, 'Failing Store')
        task_id, gate = await _make_running_task(
            admin_client, install_fake_agent, store_id
        )
        failing_browser(exc)

        r = await admin_client.post(
            f'/api/stores/{store_id}/browser/start',
            headers={'X-Vibe-Task': task_id},
        )
        # Typed body: the stable code, message and hint.
        assert r.status_code == 500
        detail = r.json()['detail']
        assert detail['code'] == exc.code
        assert detail['hint']

        # The task ended FAILED with the typed error + category.
        data = await wait_for_task(admin_client, task_id, 'failed')
        assert data['error'].startswith(f'[{exc.code}]')
        assert data['error_category'] == exc.code.lower()

        # The agent was stopped: the gate never opened, and no new
        # session is registered for the task.
        assert not gate.is_set()
        assert install_fake_agent.get_session(task_id) is None

    async def test_no_agent_turn_after_the_stop(
        self, admin_client, install_fake_agent, failing_browser
    ):
        """The stopped agent writes no further transcript message."""
        store_id = await _create_store(admin_client, 'Failing Store 2')
        task_id, gate = await _make_running_task(
            admin_client, install_fake_agent, store_id
        )
        msgs_before = len(
            (await admin_client.get(f'/api/tasks/{task_id}/messages')).json()
        )
        failing_browser(UnsupportedKernel('kernel is Firefox'))
        await admin_client.post(
            f'/api/stores/{store_id}/browser/start',
            headers={'X-Vibe-Task': task_id},
        )
        await wait_for_task(admin_client, task_id, 'failed')
        msgs_after = (
            await admin_client.get(f'/api/tasks/{task_id}/messages')
        ).json()
        # No new turn after the stop (the gated result never ran).
        assert all(m.get('role') != 'result' for m in msgs_after[msgs_before:])


class TestTransientStartFailure:
    async def test_task_keeps_running_and_body_is_typed(
        self, admin_client, install_fake_agent, failing_browser
    ):
        store_id = await _create_store(admin_client, 'Transient Store')
        task_id, gate = await _make_running_task(
            admin_client, install_fake_agent, store_id
        )
        failing_browser(ClientNotRunning('client is starting'))

        r = await admin_client.post(
            f'/api/stores/{store_id}/browser/start',
            headers={'X-Vibe-Task': task_id},
        )
        assert r.status_code == 500
        assert r.json()['detail']['code'] == 'CLIENT_NOT_RUNNING'
        # NOT failed server-side: the wrapper retries; the run lives.
        data = (await admin_client.get(f'/api/tasks/{task_id}')).json()
        assert data['status'] == 'running'

        gate.set()
        await wait_for_task(admin_client, task_id, 'completed')

    async def test_login_required_with_credentials_is_not_terminal(
        self, admin_client, install_fake_agent, failing_browser
    ):
        """Credentials existed and the login did not take: the run
        continues (a retry can succeed); only the typed line is sent."""
        store_id = await _create_store(admin_client, 'Login Store')
        task_id, gate = await _make_running_task(
            admin_client, install_fake_agent, store_id
        )
        failing_browser(
            LoginRequired('stayed logged out', credentials_stored=True)
        )
        r = await admin_client.post(
            f'/api/stores/{store_id}/browser/start',
            headers={'X-Vibe-Task': task_id},
        )
        assert r.status_code == 500
        assert r.json()['detail']['code'] == 'LOGIN_REQUIRED'
        data = (await admin_client.get(f'/api/tasks/{task_id}')).json()
        assert data['status'] == 'running'
        gate.set()
        await wait_for_task(admin_client, task_id, 'completed')


class TestHeaderOnlySelectsThisStoresLiveTask:
    """D-C2-1: the header is agent-reachable; bound what it can do."""

    async def test_other_stores_task_untouched(
        self, admin_client, install_fake_agent, failing_browser
    ):
        store_a = await _create_store(admin_client, 'Store A')
        store_b = await _create_store(admin_client, 'Store B')
        task_a, gate_a = await _make_running_task(
            admin_client, install_fake_agent, store_a
        )
        failing_browser(UnsupportedKernel('kernel is Firefox'))

        # Start store B, naming store A's task in the header.
        r = await admin_client.post(
            f'/api/stores/{store_b}/browser/start',
            headers={'X-Vibe-Task': task_a},
        )
        assert r.status_code == 500
        assert r.json()['detail']['code'] == 'UNSUPPORTED_KERNEL'
        # Store A's task was NOT failed: it still runs, agent alive.
        data = (await admin_client.get(f'/api/tasks/{task_a}')).json()
        assert data['status'] == 'running'
        assert data['error'] is None
        gate_a.set()
        await wait_for_task(admin_client, task_a, 'completed')

    async def test_completed_task_untouched(
        self, admin_client, install_fake_agent, failing_browser
    ):
        store_id = await _create_store(admin_client, 'Done Store')
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='already done'
        )
        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'finished earlier', 'store_id': store_id},
        )
        task_id = r.json()['id']
        done = await wait_for_task(admin_client, task_id, 'completed')
        assert done['status'] == 'completed'

        failing_browser(UnsupportedKernel('kernel is Firefox'))
        r = await admin_client.post(
            f'/api/stores/{store_id}/browser/start',
            headers={'X-Vibe-Task': task_id},
        )
        # The typed error still answers …
        assert r.status_code == 500
        assert r.json()['detail']['code'] == 'UNSUPPORTED_KERNEL'
        # … but the completed task is untouched.
        data = (await admin_client.get(f'/api/tasks/{task_id}')).json()
        assert data['status'] == 'completed'
        assert data['error'] is None

    async def test_unknown_task_id_ignored(self, admin_client, failing_browser):
        store_id = await _create_store(admin_client, 'Ghost Store')
        failing_browser(UnsupportedKernel('kernel is Firefox'))
        r = await admin_client.post(
            f'/api/stores/{store_id}/browser/start',
            headers={'X-Vibe-Task': 'nonexistent-task-id'},
        )
        assert r.status_code == 500
        assert r.json()['detail']['code'] == 'UNSUPPORTED_KERNEL'


class TestUntypedFailureIsZiniaosExactPath:
    """A failure outside the inject taxonomy surfaces exactly as
    today (plain 500 detail string) and never fails the task — Ziniao
    is not reclassified by accident."""

    async def test_plain_exception_keeps_old_shape(
        self, admin_client, install_fake_agent, failing_browser
    ):
        store_id = await _create_store(admin_client, 'Ziniao-Like Store')
        task_id, gate = await _make_running_task(
            admin_client, install_fake_agent, store_id
        )
        failing_browser(RuntimeError('Ziniao client is wedged'))

        r = await admin_client.post(
            f'/api/stores/{store_id}/browser/start',
            headers={'X-Vibe-Task': task_id},
        )
        assert r.status_code == 500
        detail = r.json()['detail']
        # The pre-C2 shape: a plain string, no code/hint keys.
        assert isinstance(detail, str)
        assert detail == 'Failed to start browser: Ziniao client is wedged'
        data = (await admin_client.get(f'/api/tasks/{task_id}')).json()
        assert data['status'] == 'running'
        gate.set()
        await wait_for_task(admin_client, task_id, 'completed')

    async def test_non_inject_typed_looking_error_is_not_reclassified(
        self, admin_client, install_fake_agent, failing_browser
    ):
        """An InjectError SUBCLASS that is not in the terminal policy
        (base InjectError, unknown code) answers typed but does not
        fail the task either — only declared terminal codes stop it."""
        store_id = await _create_store(admin_client, 'Mystery Store')
        task_id, gate = await _make_running_task(
            admin_client, install_fake_agent, store_id
        )
        failing_browser(InjectError('mystery failure'))
        r = await admin_client.post(
            f'/api/stores/{store_id}/browser/start',
            headers={'X-Vibe-Task': task_id},
        )
        assert r.status_code == 500
        assert r.json()['detail']['code'] == 'INJECT_ERROR'
        data = (await admin_client.get(f'/api/tasks/{task_id}')).json()
        assert data['status'] == 'running'
        gate.set()
        await wait_for_task(admin_client, task_id, 'completed')
