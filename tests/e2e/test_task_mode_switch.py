"""E2E: switching one task's Auto / Review leaves the default alone.

The task footer's Auto / Review switch changes THAT task only. The
default a new task starts in is set in Settings → General (Default
Execution Mode) and nowhere else, so the next task created through the
New Task dialog still starts in it.
"""

import re
import time

from playwright.sync_api import Page, expect
import pytest

import tests.e2e.e2e_helpers as e2e_helpers

pytestmark = [pytest.mark.e2e]


def _url(path: str) -> str:
    # Read at call time: an owned-server module re-aims BASE_URL.
    return e2e_helpers.BASE_URL.rstrip('/') + path


def _create(page: Page, prompt: str) -> dict:
    """Create a task through the New Task dialog; return the created row."""
    page.get_by_role('button', name='+ New Task').click()
    page.get_by_placeholder('e.g. Navigate to google.com').fill(prompt)
    with page.expect_response(
        lambda r: r.url.endswith('/api/tasks') and r.request.method == 'POST'
    ) as created:
        page.get_by_role('button', name='Create & Run').click()
    assert created.value.ok, created.value.text()
    return {
        'sent': created.value.request.post_data_json,
        'task': created.value.json(),
    }


def test_switching_a_task_keeps_the_default(authenticated_page: Page):
    page = authenticated_page
    me = page.request.get(_url('/api/auth/me')).json()
    original = me['plan_mode_default']
    # Precondition: the default (set in Settings) is Auto.
    page.request.patch(
        _url('/api/auth/me/profile'), data={'plan_mode_default': False}
    )
    page.reload()
    store = f'e2e-mode-{int(time.time())}'
    page.get_by_role('button', name='+ New Store').click()
    page.get_by_placeholder('Store name...').fill(store)
    page.get_by_role('button', name='Create', exact=True).click()
    page.get_by_role('button', name=re.compile(store)).first.click()
    created: list[str] = []
    try:
        first = _create(page, 'Write one line to a scratch file.')
        created.append(first['task']['id'])
        assert first['task']['plan_mode'] is False

        # Switch THIS task to Review in its footer.
        review = page.get_by_role('button', name=re.compile('^review$', re.I))
        with page.expect_response(
            lambda r: '/review-plan' in r.url and r.request.method == 'PATCH'
        ) as switched:
            review.click()
        assert switched.value.ok
        expect(review).to_have_attribute('aria-pressed', 'true')

        # The default did not move…
        me = page.request.get(_url('/api/auth/me')).json()
        assert me['plan_mode_default'] is False

        # …so the next task, from the same dialog, still starts in Auto.
        second = _create(page, 'Write another line to a scratch file.')
        created.append(second['task']['id'])
        assert second['sent'].get('plan_mode') is False, second['sent']
        assert second['task']['plan_mode'] is False
    finally:
        for task_id in created:
            page.request.post(_url(f'/api/tasks/{task_id}/agent/stop'))
        page.request.patch(
            _url('/api/auth/me/profile'),
            data={'plan_mode_default': original},
        )
