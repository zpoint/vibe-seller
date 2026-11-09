"""E2E: the task footer's live usage meter, mock CLI → SSE → browser.

The mock CLI (tests/e2e/mock_cli.py) reports per-call usage exactly as
the real CLI's partial messages do (message_start / message_delta, plus
an assistant event per block repeating the id), on ``claude-sonnet-4-5``
— priced from the bundled models.dev snapshot on the default profile.

Pins, end to end through the real backend:
- totals are metered per API call, once per call (no double count from
  the repeated assistant event, none from the cumulative ``result``);
- the cost is the catalog rate × each bucket;
- the chip renders context fill + cost, and moves LIVE (SSE) while the
  task runs, without a reload.

Requires:
  MOCK_CLI=tests/e2e/mock_cli.py ./start.sh <port>
  E2E_BASE_URL=http://localhost:<port> pytest tests/e2e/test_task_usage_ui.py
"""

import os
import re
import time

import httpx
from playwright.sync_api import expect
import pytest

from tests.e2e.conftest import BASE_URL
from tests.e2e.e2e_helpers import login

MOCK_CLI = os.environ.get('MOCK_CLI', '')
if not MOCK_CLI:
    pytest.skip(
        'MOCK_CLI not set — skipping usage meter UI tests. '
        'Run with: MOCK_CLI=tests/e2e/mock_cli.py ./start.sh 7777',
        allow_module_level=True,
    )

pytestmark = [pytest.mark.e2e]

# claude-sonnet-4-5 on models.dev: $3 in / $15 out / $0.30 cache read /
# $3.75 cache write per 1M tokens.
RATES = (3.0, 15.0, 0.3, 3.75)
# What one mock call reports (mock_cli._usage / _end_call).
CALL_INPUT, CALL_CACHE_WRITE, CALL_OUTPUT = 800, 2_000, 250


@pytest.fixture
def api():
    client = httpx.Client(timeout=30)
    login(client)
    yield client
    client.close()


def _task(api, task_id: str) -> dict:
    resp = api.get(f'{BASE_URL}/api/tasks/{task_id}')
    resp.raise_for_status()
    return resp.json()


def _wait(api, task_id: str, status: str, timeout: float = 90) -> dict:
    deadline = time.time() + timeout
    data: dict = {}
    while time.time() < deadline:
        data = _task(api, task_id)
        if data['status'] == status:
            return data
        assert data['status'] != 'failed', data.get('error')
        time.sleep(0.5)
    raise TimeoutError(f'{task_id[:8]} not {status} (last={data["status"]})')


def _expected_cost(t: dict) -> float:
    r_in, r_out, r_cr, r_cw = RATES
    return (
        t['input_tokens'] * r_in
        + t['output_tokens'] * r_out
        + t['cache_read_tokens'] * r_cr
        + t['cache_write_tokens'] * r_cw
    ) / 1e6


def test_usage_is_metered_once_per_call_and_priced(api):
    resp = api.post(
        f'{BASE_URL}/api/tasks', json={'title': f'Usage meter {time.time()}'}
    )
    resp.raise_for_status()
    task_id = resp.json()['id']
    # Non-store task → plan mode: plan, approve, execute.
    planned = _wait(api, task_id, 'planned')
    api.post(f'{BASE_URL}/api/tasks/{task_id}/execute-plan').raise_for_status()
    done = _wait(api, task_id, 'completed')

    calls = done['input_tokens'] // CALL_INPUT
    assert calls >= 2, done
    # Every call counted exactly once: each bucket is an exact multiple
    # of what ONE call reports (a double count breaks the ratio).
    assert done['input_tokens'] == calls * CALL_INPUT
    assert done['cache_write_tokens'] == calls * CALL_CACHE_WRITE
    assert done['output_tokens'] == calls * CALL_OUTPUT
    assert done['cost_basis'] == 'catalog'
    assert done['usage_model'] == 'claude-sonnet-4-5'
    assert done['cost_usd'] == pytest.approx(_expected_cost(done), rel=1e-6)
    assert done['context_window'] == 200_000
    assert 0 < done['context_tokens'] < done['context_window']
    assert done['cost_usd'] > planned['cost_usd'] > 0


def test_chip_renders_and_updates_live(api, authenticated_page):
    page = authenticated_page
    title = f'Usage chip {int(time.time())}'
    resp = api.post(f'{BASE_URL}/api/tasks', json={'title': title})
    resp.raise_for_status()
    task_id = resp.json()['id']
    planned = _wait(api, task_id, 'planned')

    page.goto(f'{BASE_URL}/tasks/{task_id}')
    chip = page.get_by_test_id('task-usage-meter')
    expect(chip).to_be_visible(timeout=15_000)
    pct = round(planned['context_tokens'] / planned['context_window'] * 100)
    expect(page.get_by_test_id('task-usage-context')).to_have_text(
        re.compile(rf'^{pct}% · .+ / 200k$')
    )
    cost = page.get_by_test_id('task-usage-cost')
    expect(cost).to_have_text(f'· ≈${planned["cost_usd"]:.2f}')

    # Execute with the page open: the figures must move by SSE alone
    # (execution continues the same CLI process, so the mock's context
    # keeps growing call by call).
    api.post(f'{BASE_URL}/api/tasks/{task_id}/execute-plan').raise_for_status()
    done = _wait(api, task_id, 'completed')
    done_pct = round(done['context_tokens'] / done['context_window'] * 100)
    assert done_pct != pct, (planned, done)
    expect(page.get_by_test_id('task-usage-context')).to_have_text(
        re.compile(rf'^{done_pct}% · '), timeout=15_000
    )
    expect(cost).to_have_text(f'· ≈${done["cost_usd"]:.2f}', timeout=15_000)

    chip.click()
    details = page.get_by_test_id('task-usage-details')
    expect(details).to_contain_text('claude-sonnet-4-5')
    expect(details).to_contain_text('models.dev')
