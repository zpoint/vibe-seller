"""Turn-lifecycle e2e against the mock CLI (process-per-turn model).

Runs in the e2e-mock-cli CI job (server started with
``MOCK_CLI=tests/e2e/mock_cli.py``). Three flows, selected by markers
in the task description (see mock_cli.py):

- ``[[MOCK_ASYNC_SUBAGENT]]`` — a well-behaved run: async subagent
  spawned, its events stream through the parent, the mock WAITS for
  the ``<task-notification>`` before its final result. Must complete
  with NO ``review_gate_redrive`` — the notification loop, not the
  redrive backstop, carries it.
- ``[[MOCK_PREMATURE_RESULT]]`` — the backstop: a result emitted while
  the subagent still runs must be intercepted (redrive), then the
  completed review + final result finish the task; the final text is
  the deliverable (last-wins).
- ``[[MOCK_LINGER_WAIT]]`` — the mock emits its result and stays alive
  until stdin EOF, exactly like the real CLI: the quiescence watchdog
  must close the turn (``turn_idle_close`` event) and the task must
  complete.
"""

import logging
import os

import httpx
import pytest

from tests.e2e.e2e_helpers import (
    create_store,
    create_task,
    get_messages,
    login,
    poll_task_status,
)

logger = logging.getLogger(__name__)

MOCK_CLI = os.environ.get('MOCK_CLI', '')
if not MOCK_CLI:
    pytest.skip(
        'MOCK_CLI not set — skipping turn-lifecycle mock tests. '
        'Run with: MOCK_CLI=tests/e2e/mock_cli.py ./start.sh 7777',
        allow_module_level=True,
    )

pytestmark = [pytest.mark.e2e]

# Generous per-test budget: the linger flow deliberately waits out the
# quiet tier (default 5s) plus watchdog tick granularity.
TASK_TIMEOUT = 180


@pytest.fixture(scope='module')
def api() -> httpx.Client:
    client = httpx.Client(timeout=30)
    login(client)
    return client


@pytest.fixture(scope='module')
def store_id(api) -> str:
    return create_store(api, 'Turn Lifecycle Mock Store')['id']


def _events(msgs: list[dict], needle: str) -> list[dict]:
    return [
        m
        for m in msgs
        if m.get('role') == 'agent_event' and needle in str(m.get('content'))
    ]


class TestAsyncSubagentFlow:
    def test_notification_loop_completes_without_redrive(self, api, store_id):
        task = create_task(
            api,
            'async subagent turn test',
            store_id=store_id,
            description=(
                '[[MOCK_ASYNC_SUBAGENT]] produce the deliverable and have '
                'a background reviewer verify it.'
            ),
            skip_reflection=True,
        )
        data = poll_task_status(
            api,
            task['id'],
            {'completed'},
            fail_statuses={'failed'},
            timeout=TASK_TIMEOUT,
        )
        assert data['status'] == 'completed'
        assert 'reviewer confirmed' in (data.get('result') or '')

        msgs = get_messages(api, task['id'])
        # The subagent's activity streamed through the parent (its
        # assistant events are re-emitted as normal messages)...
        assert any(
            m.get('role') == 'assistant'
            and 'Review passed: no gaps' in str(m.get('content'))
            for m in msgs
        )
        # ...the notification arrived...
        assert _events(msgs, 'task-notification')
        # ...and the redrive backstop was NEVER needed.
        assert not _events(msgs, 'review_gate_redrive')
        results = [m for m in msgs if m.get('role') == 'result']
        assert len(results) == 1
        assert 'reviewer confirmed' in results[0]['content']


class TestPrematureResultBackstop:
    def test_result_under_running_subagent_is_redriven(self, api, store_id):
        task = create_task(
            api,
            'premature result turn test',
            store_id=store_id,
            description=(
                '[[MOCK_PREMATURE_RESULT]] stop early while the reviewer '
                'is still running.'
            ),
            skip_reflection=True,
        )
        data = poll_task_status(
            api,
            task['id'],
            {'completed'},
            fail_statuses={'failed'},
            timeout=TASK_TIMEOUT,
        )
        assert data['status'] == 'completed'

        msgs = get_messages(api, task['id'])
        # The premature result was intercepted by the redrive...
        assert _events(msgs, 'review_gate_redrive')
        # ...so it never became a result card; only the post-review
        # final result did (last-wins deliverable).
        results = [m for m in msgs if m.get('role') == 'result']
        assert len(results) == 1
        assert 'finished after the redrive' in results[0]['content']
        assert 'finished after the redrive' in (data.get('result') or '')


class TestLingerWatchdogTerminatesTurn:
    def test_watchdog_closes_idle_turn(self, api, store_id):
        task = create_task(
            api,
            'linger watchdog turn test',
            store_id=store_id,
            description=(
                '[[MOCK_LINGER_WAIT]] finish the work and then idle like '
                'a real CLI until the platform ends the turn.'
            ),
            skip_reflection=True,
        )
        data = poll_task_status(
            api,
            task['id'],
            {'completed'},
            fail_statuses={'failed'},
            timeout=TASK_TIMEOUT,
        )
        assert data['status'] == 'completed'

        msgs = get_messages(api, task['id'])
        # The mock never exits on its own — the ONLY way this task
        # completed is the quiescence watchdog closing stdin.
        closes = _events(msgs, 'turn_idle_close')
        assert closes, 'expected a turn_idle_close agent_event'
        assert 'quiescent' in closes[0]['content']
