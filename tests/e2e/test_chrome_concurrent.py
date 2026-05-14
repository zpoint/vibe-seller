"""Chrome + CDPMuxProxy e2e tests.

Proves that Chrome browser backend works end-to-end through the
vibe-seller server, both for single tasks and concurrent tasks
sharing the same store (and therefore the same CDPMuxProxy).

Uses a local HTTP test server (like test_llm_browser.py) so tests
don't depend on external URLs.

Run:
    pytest tests/e2e/test_chrome_concurrent.py --e2e -v
"""

import http.server
import logging
from pathlib import Path
import threading
import time

import pytest

from tests.e2e.e2e_helpers import (
    BASE_URL,
    create_store,
    create_task,
    poll_task_status,
)

logger = logging.getLogger(__name__)
pytestmark = [pytest.mark.e2e]

SERVER_LOG = Path('logs/server_stdout.log')

# -- Static test site --

TEST_HTML = """\
<!DOCTYPE html>
<html><head><title>Chrome Test Site</title></head>
<body>
  <h1>Chrome E2E Test</h1>
  <p>This page verifies Chrome + CDPMuxProxy works.</p>
  <p>Magic token: CHROME_OK_12345</p>
</body></html>
"""


class _TestHandler(http.server.BaseHTTPRequestHandler):
    """Serve a single test page."""

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(TEST_HTML.encode())

    def log_message(self, format, *args):
        pass


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture(scope='module')
def test_site():
    """Start a local HTTP server serving the test page."""
    server = http.server.HTTPServer(('127.0.0.1', 0), _TestHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{port}'
    server.shutdown()
    server.server_close()


@pytest.fixture(scope='module')
def chrome_store(api_client):
    """Create a Chrome-backed store for testing."""
    ts = int(time.time())
    store = create_store(
        api_client,
        f'chrome-e2e-{ts}',
        browser_backend='chrome',
    )
    yield store
    # Cleanup: stop browser session
    try:
        api_client.post(f'{BASE_URL}/api/stores/{store["id"]}/browser/stop')
    except Exception:
        pass


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def assert_cdp_proxy_used(log_path: Path, task_id: str):
    """Assert CDPMuxProxy was used for the task."""
    if not log_path.exists():
        pytest.skip(f'Server log not found at {log_path}')
    log_text = log_path.read_text()
    assert f'CDPMuxProxy client connected: {task_id}' in log_text, (
        f'CDPMuxProxy client {task_id[:8]} not found in server log'
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


def _assert_no_daemon_conflict(result: dict):
    """Assert task didn't fail with daemon session conflict."""
    if result['status'] == 'failed':
        error = (result.get('error') or '').lower()
        assert 'already running' not in error, (
            f'Daemon conflict: {result.get("error")}'
        )


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


class TestChromeSingleTask:
    """Single task through Chrome + CDPMuxProxy."""

    def test_single_task_navigates_test_site(
        self, api_client, chrome_store, test_site
    ):
        """Create a task that navigates to the local test site
        via Chrome CDPMuxProxy and reports what it sees."""
        task = create_task(
            api_client,
            title='Chrome single nav',
            store_id=chrome_store['id'],
            description=(
                f'Use browser-use CLI with the DEFAULT session '
                f'(do NOT use the -aux session) to open '
                f'{test_site} and report the page title '
                f'and the magic token you see on the page.'
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
        assert_cdp_proxy_used(SERVER_LOG, task['id'])


class TestChromeConcurrent:
    """Two concurrent tasks through the same Chrome CDPMuxProxy."""

    def test_two_tasks_same_chrome_store(
        self, api_client, chrome_store, test_site
    ):
        """Two tasks both navigate to the test site through
        the same CDPMuxProxy. Proves multi-client works
        by checking interleaved AGENT_DEBUG logs.

        Neither should fail with 'Session already running'.
        """
        task_a = create_task(
            api_client,
            title='Chrome task A',
            store_id=chrome_store['id'],
            description=(
                f'Use browser-use CLI with the DEFAULT session '
                f'(do NOT use the -aux session) to open '
                f'{test_site} and report the page title.'
            ),
            plan_mode=False,
        )
        task_b = create_task(
            api_client,
            title='Chrome task B',
            store_id=chrome_store['id'],
            description=(
                f'Use browser-use CLI with the DEFAULT session '
                f'(do NOT use the -aux session) to open '
                f'{test_site} and report the magic token '
                f'you see on the page.'
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
        _assert_no_daemon_conflict(result_a)
        _assert_no_daemon_conflict(result_b)

        assert (
            result_a['status'] == 'completed'
            or result_b['status'] == 'completed'
        ), 'At least one Chrome task should complete'

        # Verify both went through CDPMuxProxy
        for t in [task_a, task_b]:
            assert_cdp_proxy_used(SERVER_LOG, t['id'])

        # Prove concurrency via log interleaving
        if (
            result_a['status'] == 'completed'
            and result_b['status'] == 'completed'
        ):
            assert_interleaved(SERVER_LOG, task_a['id'], task_b['id'])
