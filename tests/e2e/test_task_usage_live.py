"""E2E (real agent): the usage meter against a real provider.

The mock suite (test_task_usage_ui.py) pins the arithmetic on a fixed
stream; this one pins that a REAL CLI + provider stream produces a
metered, priced, windowed task — whatever provider E2E_PROVIDER_MAP
assigns this worker. Every built-in preset maps to a models.dev
provider (tests/unit/test_model_pricing.py), so a run on a preset's
endpoint must come out priced (``catalog``) or plan-``included``; only a
custom endpoint (a base URL no preset uses) may be ``unpriced``.

Requires: provider credentials matching E2E_PROVIDER_MAP plus the
``claude`` CLI on PATH (skipped under MOCK_CLI).
"""

import os
import time

import httpx
from playwright.sync_api import Page, expect
import pytest

from tests.e2e.conftest import BASE_URL
from tests.e2e.e2e_helpers import (
    PIPELINE_TIMEOUT,
    create_store,
    create_task,
    fetch_presets,
    poll_task_status,
)

if os.environ.get('MOCK_CLI'):
    pytest.skip(
        'real-agent usage test; the mock suite covers MOCK_CLI',
        allow_module_level=True,
    )

pytestmark = [pytest.mark.e2e]


def _on_preset_endpoint(api: httpx.Client, profile_id: str | None) -> bool:
    """Whether the run's profile talks to a built-in preset's base URL
    (or to api.anthropic.com — no base URL)."""
    profiles = api.get(f'{BASE_URL}/api/profiles').json()['profiles']
    profile = next(
        (p for p in profiles if p['id'] == (profile_id or 'default')), {}
    )
    base = ((profile.get('env') or {}).get('ANTHROPIC_BASE_URL') or '').strip()
    if not base:
        return True
    preset_urls = {
        (p.get('env') or {}).get('ANTHROPIC_BASE_URL', '').strip()
        for p in fetch_presets(api).values()
    }
    return base in preset_urls


def test_a_real_run_is_metered_priced_and_shown(
    api_client: httpx.Client, authenticated_page: Page
):
    store = create_store(
        api_client, f'usage-live-{int(time.time())}', browser_backend='chrome'
    )
    task = create_task(
        api_client,
        title='What day of the week is 1 January 2027?',
        store_id=store['id'],
        description='Just tell me the weekday, no need to open a browser.',
        plan_mode=False,
    )
    done = poll_task_status(
        api_client,
        task['id'],
        {'completed'},
        fail_statuses={'failed'},
        timeout=PIPELINE_TIMEOUT,
    )
    assert done['status'] == 'completed', done.get('error')

    # Metered: a real run has a prompt and an answer.
    assert done['input_tokens'] + done['cache_read_tokens'] > 0, done
    assert done['output_tokens'] > 0, done
    assert done['usage_model'], done
    # Windowed: the main agent's context fits the model's window.
    assert done['context_window'] and done['context_window'] >= 100_000
    assert 0 < done['context_tokens'] <= done['context_window']
    # Priced: a preset endpoint is never "unpriced".
    if _on_preset_endpoint(api_client, done['ai_profile_id']):
        assert done['cost_basis'] in ('catalog', 'included'), done
    else:
        assert done['cost_basis'] == 'unpriced', done
    if done['cost_basis'] == 'catalog':
        assert done['cost_usd'] > 0
    else:
        assert done['cost_usd'] == 0

    page = authenticated_page
    page.goto(f'{BASE_URL}/stores/{store["id"]}/tasks/{task["id"]}')
    expect(page.get_by_test_id('task-usage-context')).to_contain_text(
        '%', timeout=15_000
    )
