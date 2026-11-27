"""Tier-1: a terminal browser-start failure ends the task (C2, mock CLI).

The full chain on a real server: a store task whose agent (the mock
CLI) RUNS the store's browser-use wrapper, the wrapper's lazy start
hits the real start route, the fake vendor's backend
(``tests/e2e/fixtures/fake_start_vendor``, registered through the
real plugin entry point — the server must run with
``FAKE_START_VENDOR=1``) raises ``UnsupportedKernel``, and the server
must:

- end the task FAILED with the typed error and the
  ``unsupported_kernel`` category (the docs-link fields the frontend
  maps),
- log the terminal-stop line,
- stop the agent mid-wrapper-call, so NOTHING the mock would do after
  the wrapper call (its follow-up tool call and result) reaches the
  transcript.

Before the fix (on main), the start failure reached the agent as a
bare ``HTTP 500``, the task kept running and the mock's follow-up
work landed — that is the negative control, run by hand on the parent
commit.
"""

import os
import time

import httpx
import pytest

from tests.e2e.e2e_helpers import BASE_URL, create_store, login_client, run_tag

MOCK_CLI = os.environ.get('MOCK_CLI', '')
if not MOCK_CLI:
    pytest.skip(
        'MOCK_CLI not set — the browser-start-failure e2e needs the mock CLI',
        allow_module_level=True,
    )

pytestmark = [pytest.mark.e2e]

#: The mock-CLI marker whose scenario executes the store wrapper (see
#: ``tests/e2e/mock_cli.py::run_store_browser_start``).
STORE_BROWSER_MARKER = '[[MOCK_STORE_BROWSER]]'

#: Where the owned/shared server's stdout log lives, when the harness
#: names it (CI: ``logs/mock_server_stdout.log``). The terminal-stop
#: line is asserted when the file is readable.
SERVER_LOG = os.environ.get('MOCK_SERVER_LOG', '')

POLL_TIMEOUT_S = 180.0


@pytest.fixture(scope='module')
def api():
    client = httpx.Client(timeout=60)
    login_client(client, BASE_URL)
    yield client
    client.close()


def _get_task(api, task_id: str) -> dict:
    return api.get(f'{BASE_URL}/api/tasks/{task_id}').json()


def _wait_status(api, task_id: str, wanted: str) -> dict:
    deadline = time.time() + POLL_TIMEOUT_S
    data: dict = {}
    while time.time() < deadline:
        data = _get_task(api, task_id)
        if data.get('status') in (wanted, 'failed'):
            return data
        time.sleep(1.0)
    raise AssertionError(
        f'task {task_id} never reached {wanted} within '
        f'{POLL_TIMEOUT_S}s (last: {data.get("status")!r})'
    )


class TestTerminalStartFailureStopsTheAgent:
    def test_task_fails_typed_and_no_turn_after(self, api):
        # A store on the fake vendor — its start always raises
        # UNSUPPORTED_KERNEL on a FAKE_START_VENDOR server.
        store = create_store(
            api, f'fakestart-{run_tag()}', browser_backend='fakestart'
        )
        store_id = store['id']

        # The task prompt carries the marker: the mock agent executes
        # the store wrapper for real and would carry on afterwards.
        r = api.post(
            f'{BASE_URL}/api/tasks',
            json={
                'title': 'open the store backend home page',
                'description': (f'{STORE_BROWSER_MARKER} Open {store["name"]}'),
                'store_id': store_id,
            },
        )
        r.raise_for_status()
        task_id = r.json()['id']

        data = _wait_status(api, task_id, 'failed')
        # Typed failure on the task row + the docs-link fields in the
        # API response (what the frontend's banner + docsHelp map).
        assert data['error'].startswith('[UNSUPPORTED_KERNEL]'), data['error']
        assert data['error_category'] == 'unsupported_kernel'

        # The agent was stopped INSIDE the wrapper's start call: the
        # mock's scripted follow-up work (its "still-alive" tool call
        # and its final result) never reached the transcript.
        msgs = api.get(f'{BASE_URL}/api/tasks/{task_id}/messages').json()
        body = '\n'.join(str(m.get('content') or '') for m in msgs)
        assert 'still-alive' not in body
        assert data.get('result') is None

        # The server logged the terminal stop (asserted when the
        # harness points MOCK_SERVER_LOG at the server's stdout). The
        # line is written AFTER the agent stop completes (it audits
        # the completed stop), which trails the task's FAILED flip by
        # the stop's graceful-interrupt window — so poll for it
        # briefly rather than reading once.
        if SERVER_LOG and os.path.isfile(SERVER_LOG):
            deadline = time.time() + 20.0
            while time.time() < deadline:
                log = open(
                    SERVER_LOG, encoding='utf-8', errors='replace'
                ).read()
                if 'terminal browser-start failure' in log:
                    break
                time.sleep(1.0)
            assert 'terminal browser-start failure' in log, (
                'the terminal-stop log line is missing'
            )
