"""Ziniao browser e2e tests — LOCAL ONLY.

Tests the full Ziniao stack: wrapper → daemon → CDPMuxProxy → browser.
Requires a running Ziniao instance on the host machine.

Credentials via environment variables (NEVER in code):
    ZINIAO_COMPANY=...
    ZINIAO_USERNAME=...
    ZINIAO_PASSWORD=...
    ZINIAO_BROWSER_OAUTH=...   (browser ID or OAuth token)
    ZINIAO_SOCKET_PORT=16851   (optional, default 16851)

Run locally:
    pytest tests/e2e/test_ziniao_browser.py --e2e -v -m ziniao

Run in Docker (Ziniao on host, container uses host networking):
    ZINIAO_COMPANY=myco ZINIAO_USERNAME=user ZINIAO_PASSWORD=pass \\
      ZINIAO_BROWSER_OAUTH=12345 \\
      MINIMAX_API_KEY=sk-... E2E_WORKERS=1 E2E_PROVIDER_MAP=minimax \\
      docker compose -f docker/docker-compose.yml run --rm e2e \\
      uv run pytest tests/e2e/test_ziniao_browser.py --e2e -v -m ziniao

These tests are NEVER triggered in CI.
Only navigates to baidu.com — no production pages.
"""

import logging
import os
from pathlib import Path
import time

import httpx
import pytest

from tests.e2e.e2e_helpers import (
    BASE_URL,
    create_store,
    create_task,
    login,
    poll_task_status,
)

logger = logging.getLogger(__name__)
pytestmark = [pytest.mark.e2e, pytest.mark.ziniao]

SERVER_LOG = Path('logs/server_stdout.log')


def _skip_if_no_ziniao():
    """Skip if Ziniao env vars not set."""
    if not os.environ.get('ZINIAO_COMPANY'):
        pytest.skip('ZINIAO_COMPANY not set — skipping Ziniao tests')


def assert_cdp_proxy_used(log_path: Path, task_id: str):
    """Assert the CDPMuxProxy was used (not the -aux Chrome path).

    Checks server log for 'CDPMuxProxy client connected: {task_id}'
    proving traffic went through the Ziniao proxy, not Chrome aux.
    The client_id logged by CDPMuxProxy is the full UUID (extracted
    from the /client-{task_id} WebSocket path).
    """
    if not log_path.exists():
        pytest.skip(f'Server log not found at {log_path}')
    log_text = log_path.read_text()
    # CDPMuxProxy logs: "CDPMuxProxy client connected: {uuid}"
    assert f'CDPMuxProxy client connected: {task_id}' in log_text, (
        f'CDPMuxProxy client {task_id[:8]} not found in server '
        f'log — task may have used the -aux Chrome session '
        f'instead of the Ziniao CDP proxy'
    )


def assert_interleaved(log_path: Path, id_a: str, id_b: str):
    """Assert AGENT_DEBUG logs for two tasks are interleaved."""
    prefix_a, prefix_b = id_a[:8], id_b[:8]
    entries: list[str] = []
    if not log_path.exists():
        pytest.skip(f'Server log not found at {log_path}')
    for line in log_path.read_text().splitlines():
        if f'AGENT_DEBUG [{prefix_a}]' in line:
            entries.append('A')
        elif f'AGENT_DEBUG [{prefix_b}]' in line:
            entries.append('B')
    assert 'A' in entries and 'B' in entries, (
        'Both tasks must have AGENT_DEBUG log entries'
    )
    transitions = sum(
        1 for i in range(1, len(entries)) if entries[i] != entries[i - 1]
    )
    assert transitions >= 2, (
        f'Insufficient interleaving ({transitions} transitions). '
        f'Sequence: {"".join(entries[:30])}'
    )


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture(scope='module')
def api_client():
    """Authenticated httpx client."""
    client = httpx.Client(timeout=30)
    login(client)
    yield client
    client.close()


@pytest.fixture(scope='module')
def ziniao_account(api_client):
    """Create a ZiniaoAccount via API from env vars.

    The server uses this to auto-launch Ziniao and log in —
    no manual interaction needed.
    """
    _skip_if_no_ziniao()
    resp = api_client.post(
        f'{BASE_URL}/api/ziniao-accounts',
        json={
            'name': f'e2e-ziniao-{int(time.time())}',
            'company': os.environ['ZINIAO_COMPANY'],
            'username': os.environ['ZINIAO_USERNAME'],
            'password': os.environ['ZINIAO_PASSWORD'],
            'socket_port': int(os.environ.get('ZINIAO_SOCKET_PORT', '16851')),
            'client_path': os.environ.get('ZINIAO_CLIENT_PATH', 'ziniao'),
        },
    )
    resp.raise_for_status()
    return resp.json()


@pytest.fixture(scope='module')
def ziniao_store(api_client, ziniao_account):
    """Create a Ziniao store linked to the test account.

    BrowserManager reads credentials from the linked
    ZiniaoAccount — no need to stuff them into browser_config.
    """
    ts = int(time.time())
    store = create_store(
        api_client,
        f'ziniao-test-{ts}',
        browser_backend='ziniao',
        ziniao_account_id=ziniao_account['id'],
        browser_oauth=os.environ.get('ZINIAO_BROWSER_OAUTH', ''),
    )
    yield store
    # Cleanup: stop browser session
    try:
        api_client.post(f'{BASE_URL}/api/stores/{store["id"]}/browser/stop')
    except Exception:
        pass


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


class TestZiniaoSingleTask:
    """Single task through the full Ziniao stack."""

    def test_single_task_navigates_baidu(self, api_client, ziniao_store):
        """Create a task that navigates to baidu.com via
        the Ziniao CDP proxy and reports the page title."""
        task = create_task(
            api_client,
            title='Ziniao baidu test',
            store_id=ziniao_store['id'],
            description=(
                'Use browser-use CLI with the DEFAULT session '
                '(do NOT use the -aux session) to open '
                'https://www.baidu.com and report the '
                'page title. Just tell me what the title is.'
            ),
            plan_mode=False,
        )
        result = poll_task_status(
            api_client,
            task['id'],
            {'completed', 'failed'},
        )
        assert result['status'] == 'completed', (
            f'Task failed: {result.get("error")}'
        )
        # Verify traffic went through CDPMuxProxy, not -aux
        assert_cdp_proxy_used(SERVER_LOG, task['id'])


class TestZiniaoConcurrent:
    """Two concurrent tasks through CDPMuxProxy."""

    def test_two_tasks_same_ziniao_store(self, api_client, ziniao_store):
        """Two tasks both navigate to baidu.com through
        the same CDPMuxProxy. Proves multi-client works
        by checking interleaved AGENT_DEBUG logs.

        Neither should fail with 'Session already running'.
        """
        task_a = create_task(
            api_client,
            title='Ziniao task A',
            store_id=ziniao_store['id'],
            description=(
                'Use browser-use CLI with the DEFAULT session '
                '(do NOT use the -aux session) to open '
                'https://www.baidu.com and report the '
                'page title text.'
            ),
            plan_mode=False,
        )
        task_b = create_task(
            api_client,
            title='Ziniao task B',
            store_id=ziniao_store['id'],
            description=(
                'Use browser-use CLI with the DEFAULT session '
                '(do NOT use the -aux session) to open '
                'https://www.baidu.com and report how '
                'many links are on the page.'
            ),
            plan_mode=False,
        )
        result_a = poll_task_status(
            api_client,
            task_a['id'],
            {'completed', 'failed'},
        )
        result_b = poll_task_status(
            api_client,
            task_b['id'],
            {'completed', 'failed'},
        )
        # No daemon session conflicts
        for r in [result_a, result_b]:
            if r['status'] == 'failed':
                error = (r.get('error') or '').lower()
                assert 'already running' not in error, (
                    f'Daemon conflict: {r.get("error")}'
                )
        assert (
            result_a['status'] == 'completed'
            or result_b['status'] == 'completed'
        ), 'At least one Ziniao task should complete'
        # Verify both went through CDPMuxProxy
        for t in [task_a, task_b]:
            assert_cdp_proxy_used(SERVER_LOG, t['id'])
        # Prove concurrency via log interleaving
        if (
            result_a['status'] == 'completed'
            and result_b['status'] == 'completed'
        ):
            assert_interleaved(SERVER_LOG, task_a['id'], task_b['id'])


class TestZiniaoAutoStart:
    """Wrapper auto-starts CDP proxy on demand."""

    def test_task_triggers_auto_start(self, api_client, ziniao_store):
        """Stop the browser session, then create a task.
        The wrapper should auto-start the CDP proxy via
        the backend API call."""
        # Stop browser session first
        api_client.post(
            f'{BASE_URL}/api/stores/{ziniao_store["id"]}/browser/stop'
        )
        time.sleep(2)  # Give proxy time to stop

        # Now create a task — wrapper should auto-start
        task = create_task(
            api_client,
            title='Auto-start test',
            store_id=ziniao_store['id'],
            description=(
                'Use browser-use CLI with the DEFAULT session '
                '(do NOT use the -aux session) to open '
                'https://www.baidu.com and report the '
                'page title.'
            ),
            plan_mode=False,
        )
        result = poll_task_status(
            api_client,
            task['id'],
            {'completed', 'failed'},
        )
        assert result['status'] == 'completed', (
            f'Auto-start failed: {result.get("error")}'
        )
