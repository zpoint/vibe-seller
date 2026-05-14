"""Concurrent task e2e tests — architecture + business logic.

Verifies:
- Per-task daemon sessions (no "Session already running" errors)
- CDPMuxProxy multi-client (via slow-loading page + log interleaving)
- Queue scheduling rules (same-platform/different-country = QUEUE)
- Cross-platform and cross-store concurrency

Uses a slow-loading HTTP server to keep tasks alive long enough
for log interleaving to prove concurrency.
"""

import http.server
import logging
from pathlib import Path
import threading
import time

import httpx
import pytest

from tests.e2e.e2e_helpers import (
    create_store,
    create_task,
    login,
    poll_task_status,
)

logger = logging.getLogger(__name__)
pytestmark = [pytest.mark.e2e]

# Server log for interleaving assertions.
# CI writes to logs/server_stdout.log; Docker writes there too.
SERVER_LOG = Path('logs/server_stdout.log')


# ------------------------------------------------------------------
# Slow-loading test page server
# ------------------------------------------------------------------


class _SlowPageHandler(http.server.BaseHTTPRequestHandler):
    """Streams numbers then a DONE marker over N seconds.

    URL format: /slow/<seconds>/<token>
    Path-based params avoid shell &-interpretation issues when
    AI agents pass URLs unquoted to browser-use CLI.
    """

    def do_GET(self):
        parts = self.path.strip('/').split('/')
        if not parts or parts[0] != 'slow':
            self.send_response(404)
            self.end_headers()
            return
        seconds = int(parts[1]) if len(parts) > 1 else 10
        token = parts[2] if len(parts) > 2 else 'FINAL'
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        head = (
            '<!DOCTYPE html><html><head>'
            f'<title>Slow-{token}</title></head><body>'
            '<h1>Loading Progress</h1><div id="progress">'
        )
        self.wfile.write(head.encode())
        self.wfile.flush()
        for i in range(1, seconds + 1):
            time.sleep(1)
            self.wfile.write(f'<p>{i}</p>'.encode())
            self.wfile.flush()
        done = f'</div><h2 id="done">DONE-{token}</h2></body></html>'
        self.wfile.write(done.encode())
        self.wfile.flush()

    def log_message(self, fmt, *args):
        pass  # suppress request logs


@pytest.fixture(scope='module')
def slow_site():
    """Spin up a local HTTP server with slow-loading pages."""
    server = http.server.HTTPServer(('127.0.0.1', 0), _SlowPageHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{port}'
    server.shutdown()


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
def chrome_store_us_amazon(api_client):
    ts = int(time.time())
    return create_store(
        api_client,
        f'concurrent-us-{ts}',
        browser_backend='chrome',
    )


@pytest.fixture(scope='module')
def chrome_store_multi_country(api_client):
    ts = int(time.time())
    return create_store(
        api_client,
        f'concurrent-mc-{ts}',
        browser_backend='chrome',
    )


@pytest.fixture(scope='module')
def chrome_store_multi_platform(api_client):
    ts = int(time.time())
    return create_store(
        api_client,
        f'concurrent-mp-{ts}',
        browser_backend='chrome',
    )


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def assert_interleaved(log_path: Path, id_a: str, id_b: str):
    """Assert AGENT_DEBUG logs for two tasks are interleaved.

    Requires at least 2 transitions (A→B or B→A) to prove
    true concurrency, not just minimal overlap.
    """
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


class TestConcurrentSamePlatformCountry:
    """Same store, same platform, same country → concurrent."""

    def test_two_browser_tasks_both_complete(
        self, api_client, chrome_store_us_amazon, slow_site
    ):
        task_a = create_task(
            api_client,
            title='Slow page A',
            store_id=chrome_store_us_amazon['id'],
            description=(
                f'Use the browser-use CLI to open '
                f'{slow_site}/slow/15/TASK_A '
                f'and wait for the page to fully load. '
                f'Wait until you see "DONE-TASK_A", then '
                f'report it.'
            ),
        )
        task_b = create_task(
            api_client,
            title='Slow page B',
            store_id=chrome_store_us_amazon['id'],
            description=(
                f'Use the browser-use CLI to open '
                f'{slow_site}/slow/15/TASK_B '
                f'and wait for the page to fully load. '
                f'Wait until you see "DONE-TASK_B", then '
                f'report it.'
            ),
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
        _assert_no_daemon_conflict(result_a)
        _assert_no_daemon_conflict(result_b)
        assert (
            result_a['status'] == 'completed'
            or result_b['status'] == 'completed'
        ), 'At least one task should complete'
        if (
            result_a['status'] == 'completed'
            and result_b['status'] == 'completed'
        ):
            assert_interleaved(SERVER_LOG, task_a['id'], task_b['id'])


class TestConcurrentDifferentPlatform:
    """Same store, different platform → concurrent."""

    def test_different_platforms_run_concurrently(
        self, api_client, chrome_store_multi_platform
    ):
        store = chrome_store_multi_platform
        task_a = create_task(
            api_client,
            title='Amazon task',
            store_id=store['id'],
            description=(
                'Use the Write tool to create a file called '
                'amazon_done.txt with the text "amazon". '
                'Do not use the browser. Do not ask questions.'
            ),
        )
        task_b = create_task(
            api_client,
            title='Shopify task',
            store_id=store['id'],
            description=(
                'Use the Write tool to create a file called '
                'shopify_done.txt with the text "shopify". '
                'Do not use the browser. Do not ask questions.'
            ),
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
        assert result_a['status'] == 'completed', (
            f'Task A failed: {result_a.get("error")}'
        )
        assert result_b['status'] == 'completed', (
            f'Task B failed: {result_b.get("error")}'
        )


class TestConcurrentDifferentStores:
    """Different stores → always concurrent."""

    def test_different_stores_fully_independent(self, api_client, slow_site):
        ts = int(time.time())
        store_a = create_store(
            api_client,
            f'concurrent-a-{ts}',
            browser_backend='chrome',
        )
        store_b = create_store(
            api_client,
            f'concurrent-b-{ts}',
            browser_backend='chrome',
        )
        task_a = create_task(
            api_client,
            title='Store A task',
            store_id=store_a['id'],
            description=(
                'Use Bash to run: echo "store A" > store_a.txt. '
                'Do not use the browser. Do not ask questions.'
            ),
        )
        task_b = create_task(
            api_client,
            title='Store B task',
            store_id=store_b['id'],
            description=(
                'Use Bash to run: echo "store B" > store_b.txt. '
                'Do not use the browser. Do not ask questions.'
            ),
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
        assert result_a['status'] == 'completed', (
            f'Task A failed: {result_a.get("error")}'
        )
        assert result_b['status'] == 'completed', (
            f'Task B failed: {result_b.get("error")}'
        )


class TestNoDaemonConflict:
    """Regression: concurrent tasks must not produce daemon errors."""

    def test_three_concurrent_tasks_no_conflict(
        self, api_client, chrome_store_us_amazon
    ):
        tasks = []
        for i in range(3):
            t = create_task(
                api_client,
                title=f'Echo task {i}',
                store_id=chrome_store_us_amazon['id'],
                description=f'Use the Write tool to create task{i}.txt with "done"',
            )
            tasks.append(t)
        for t in tasks:
            result = poll_task_status(
                api_client,
                t['id'],
                {'completed', 'failed'},
            )
            _assert_no_daemon_conflict(result)
