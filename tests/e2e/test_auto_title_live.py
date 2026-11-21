"""E2E (real agent): the AI title replaces the placeholder.

The mock suite pins the one-box dialogs and the derived placeholder
(test_create_task_dialog.py, test_schedule_review_ui.py::TestOneBox);
the mock CLI generates no title, so the AI upgrade is only exercised
here, on the provider E2E_PROVIDER_MAP assigns this worker — the same
profile the task (or the schedule's fires) runs on.

The prompts are QUESTIONS on purpose: sent bare, the title model
answered them instead of naming them ("73 days; ..."), which either
left the placeholder or stored the answer as the title
(app/task_title.py ``title_request``). So each test asserts the title
is a name, not the answer.

Requires: provider credentials matching E2E_PROVIDER_MAP plus the
``claude`` CLI on PATH (skipped under MOCK_CLI).
"""

import os
import re
import time

from playwright.sync_api import Page, expect
import pytest

import tests.e2e.e2e_helpers as e2e_helpers

if os.environ.get('MOCK_CLI'):
    pytest.skip(
        'the mock CLI generates no AI title; the mock suite covers the '
        'placeholder',
        allow_module_level=True,
    )

pytestmark = [pytest.mark.e2e]

# 2026-01-01 → 2026-03-15 is 73 days, and 2026-03-15 is a Sunday: a
# title holding either is the answer, not a name for the task.
FIRST = 'How many days are there from 2026-01-01 to 2026-03-15 in total?'
PROMPT = f'{FIRST} And which weekday is 2026-03-15?'
PLACEHOLDER = FIRST[:49] + '…'
ANSWER = re.compile(r'\b73\b|sunday', re.I)
# generate_title gives up after 60s (GENERATE_TIMEOUT_S); then the
# placeholder stays for good.
TITLE_WAIT_S = 75


def _url(path: str) -> str:
    # Read at call time: an owned-server module re-aims BASE_URL.
    return e2e_helpers.BASE_URL.rstrip('/') + path


def _store(page: Page, tag: str) -> str:
    name = f'e2e-{tag}-{int(time.time())}'
    r = page.request.post(_url('/api/stores'), data={'name': name})
    assert r.ok, r.text()
    return r.json()['id']


def _ai_title(page: Page, path: str) -> str:
    """Wait until the row's title is no longer the placeholder."""
    deadline = time.time() + TITLE_WAIT_S
    title = PLACEHOLDER
    while time.time() < deadline:
        title = page.request.get(_url(path)).json()['title']
        if title != PLACEHOLDER:
            break
        time.sleep(1)
    assert title != PLACEHOLDER, 'the AI title never replaced the placeholder'
    return title


def _is_a_name(title: str) -> None:
    assert title.strip(), title
    assert len(title) <= 50, title
    assert not title.endswith('…'), title
    assert not ANSWER.search(title), f'the title answers the task: {title!r}'


def _stop(page: Page, task_id: str) -> None:
    page.request.post(_url(f'/api/tasks/{task_id}/agent/stop'))


def test_task_prompt_gets_an_ai_title(authenticated_page: Page):
    page = authenticated_page
    store_id = _store(page, 'ai-title')
    page.goto(_url(f'/stores/{store_id}'))
    page.get_by_role('button', name='+ New Task').click()
    page.locator('#task-prompt').fill(PROMPT)
    with page.expect_response(
        lambda r: r.url.endswith('/api/tasks') and r.request.method == 'POST'
    ) as created:
        page.get_by_role('button', name='Create & Run').click()
    assert created.value.ok, created.value.text()
    assert not created.value.request.post_data_json.get('title')
    task = created.value.json()
    try:
        assert task['title'] == PLACEHOLDER
        title = _ai_title(page, f'/api/tasks/{task["id"]}')
        _is_a_name(title)
        # Pushed over SSE (task_title): shown without a reload.
        expect(page.locator('h2').filter(has_text=title).first).to_be_visible()
    finally:
        _stop(page, task['id'])


def test_schedule_prompt_gets_an_ai_title_its_cron_fires_carry(
    authenticated_page: Page,
):
    page = authenticated_page
    store_id = _store(page, 'ai-title-sched')
    page.goto(_url(f'/stores/{store_id}/schedules'))
    page.get_by_role('button', name='New Schedule').first.click()
    page.locator('#schedule-prompt').fill(PROMPT)
    # Every minute: the cron grid is anchored to created_at, so the
    # first fire comes ~60s in — after the AI title has landed.
    page.locator('select').filter(
        has=page.locator('option[value="minutes"]')
    ).select_option('minutes')
    with page.expect_response(
        lambda r: r.url.endswith('/api/schedules')
        and r.request.method == 'POST'
    ) as created:
        page.get_by_role('button', name='Create').click()
    assert created.value.ok, created.value.text()
    assert 'title' not in created.value.request.post_data_json
    schedule = created.value.json()
    assert schedule['schedule_type'] == 'minutes'
    assert schedule['interval_value'] == 1
    fired: list[dict] = []
    try:
        assert schedule['title'] == PLACEHOLDER
        page.goto(_url(f'/stores/{store_id}/schedules/{schedule["id"]}'))
        title = _ai_title(page, f'/api/schedules/{schedule["id"]}')
        _is_a_name(title)
        # Pushed over SSE (schedule_title): shown without a reload.
        expect(page.get_by_role('heading', name=title)).to_be_visible()

        # The CRON fire (not Run Now, which reads the row in the route):
        # APScheduler registered the job with the placeholder, so the
        # fire must name its task from the schedule row.
        deadline = time.time() + 120
        while time.time() < deadline and not fired:
            fired = page.request.get(
                _url(f'/api/schedules/{schedule["id"]}/tasks')
            ).json()
            time.sleep(2)
        assert fired, 'the every-minute schedule never fired'
        assert fired[0]['title'] == title
    finally:
        page.request.delete(_url(f'/api/schedules/{schedule["id"]}'))
        for t in fired:
            _stop(page, t['id'])
