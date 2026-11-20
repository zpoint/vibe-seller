"""E2E browser test for schedules: auto by default, review on request.

What the user sees, end to end, on a real server with the mock CLI as
the agent (no model credits):

  - the create form's "Let me review the plan before it runs" box is
    OFF by default; an auto schedule runs its instructions on Run now;
  - a review schedule's plan comes back for approval: a banner at the
    very top of the app says so, one click opens the plan, Approve makes
    it ready;
  - Request changes sends the plan back and a new plan is written.

**This module owns its server** (``MOCK_CLI`` + its own ``VIBE_HOME``):
the banner counts every plan waiting on the server, so a shared server
with someone else's pending plan would make the count meaningless.

Run:
  .venv/bin/python -m pytest --e2e tests/e2e/test_schedule_review_ui.py
"""

from pathlib import Path
import time

import httpx
from playwright.sync_api import expect
import pytest

from tests.e2e import e2e_helpers
from tests.e2e.own_server import owned_agent_stack

pytestmark = [pytest.mark.e2e]

MOCK_CLI = str(Path(__file__).with_name('mock_cli.py'))
PLAN = '## Weekly check\n1. Open the dashboard\n2. Report anything odd'


@pytest.fixture(scope='module', autouse=True)
def own_stack(tmp_path_factory):
    with owned_agent_stack(
        tmp_path_factory,
        'schedule-review-ui',
        env={
            'MOCK_CLI': MOCK_CLI,
            'MOCK_CLI_SCENARIO': (
                '{"plan": "## Weekly check\\n1. Open the dashboard\\n'
                '2. Report anything odd", "result": "Checked."}'
            ),
        },
    ) as server:
        yield server


@pytest.fixture
def api():
    client = httpx.Client(base_url=e2e_helpers.BASE_URL, timeout=30)
    r = client.post('/api/auth/login', json=e2e_helpers.admin_credentials())
    r.raise_for_status()
    yield client
    for s in client.get('/api/schedules').json():
        if not s.get('is_system'):
            client.delete(f'/api/schedules/{s["id"]}')
    client.close()


def _wait_status(api, schedule_id, target, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = api.get(f'/api/schedules/{schedule_id}').json()
        if s['plan_status'] == target:
            return s
        time.sleep(0.5)
    raise AssertionError(
        f'{schedule_id} stuck at {s["plan_status"]!r}, wanted {target!r}'
    )


def _store(api, name):
    return e2e_helpers.create_store(api, name)['id']


def _open_schedule(page, store_id, schedule_id):
    page.goto(
        f'{e2e_helpers.BASE_URL}/stores/{store_id}/schedules/{schedule_id}'
    )


class TestCreateForm:
    def test_review_box_is_off_by_default_and_auto_runs(
        self, authenticated_page, api
    ):
        page = authenticated_page
        store_id = _store(api, 'UI Auto Store')
        page.goto(f'{e2e_helpers.BASE_URL}/stores/{store_id}/schedules')
        page.get_by_role('button', name='New Schedule').first.click()
        box = page.get_by_test_id('schedule-review-plan')
        expect(box).to_be_visible()
        expect(box).not_to_be_checked()
        page.get_by_placeholder('Enter task title').fill('Auto daily check')
        page.locator('textarea').first.fill('Open the dashboard.')
        page.get_by_role('button', name='Create').click()

        deadline = time.time() + 30
        created = None
        while time.time() < deadline and created is None:
            created = next(
                (
                    s
                    for s in api.get('/api/schedules').json()
                    if s['title'] == 'Auto daily check'
                ),
                None,
            )
            time.sleep(0.3)
        assert created, 'the form did not create the schedule'
        assert created['plan_mode'] is False
        assert created['plan_status'] == 'none'

        # No plan panel for an auto schedule; Run now works at once.
        _open_schedule(page, store_id, created['id'])
        expect(page.get_by_test_id('schedule-plan-panel')).to_have_count(0)
        page.get_by_role('button', name='Run Now').click()
        deadline = time.time() + 60
        runs = []
        while time.time() < deadline and not runs:
            runs = api.get(f'/api/schedules/{created["id"]}/tasks').json()
            time.sleep(0.5)
        assert runs, 'Run now created no task'
        assert runs[0]['plan_mode'] is False


class TestReviewFlow:
    def test_banner_open_approve(self, authenticated_page, api):
        page = authenticated_page
        store_id = _store(api, 'UI Review Store')
        r = api.post(
            '/api/schedules',
            json={
                'title': 'Weekly review check',
                'description': 'Open the dashboard and report anything odd.',
                'store_id': store_id,
                'schedule_type': 'weekly',
                'schedule_time': '09:00',
                'plan_mode': True,
            },
        )
        assert r.status_code == 201, r.text
        schedule_id = r.json()['id']
        _wait_status(api, schedule_id, 'pending_approval')

        page.goto(e2e_helpers.BASE_URL)
        banner = page.get_by_test_id('plan-approval-banner')
        expect(banner).to_be_visible()
        expect(banner).to_contain_text('waiting for your approval')
        banner.click()

        panel = page.get_by_test_id('schedule-plan-panel')
        expect(panel).to_be_visible()
        expect(page.get_by_test_id('plan-proposed-markdown')).to_contain_text(
            'Open the dashboard'
        )
        page.get_by_test_id('plan-approve').click()
        expect(page.get_by_test_id('schedule-plan-status')).to_have_text(
            'Ready'
        )
        expect(banner).to_have_count(0)
        assert api.get(f'/api/schedules/{schedule_id}').json()['plan'] == PLAN

    def test_request_changes_replans(self, authenticated_page, api):
        page = authenticated_page
        store_id = _store(api, 'UI Changes Store')
        r = api.post(
            '/api/schedules',
            json={
                'title': 'Send-back check',
                'description': 'Open the dashboard.',
                'store_id': store_id,
                'schedule_type': 'days',
                'schedule_time': '09:00',
                'plan_mode': True,
            },
        )
        schedule_id = r.json()['id']
        first = _wait_status(api, schedule_id, 'pending_approval')

        _open_schedule(page, store_id, schedule_id)
        page.get_by_test_id('plan-request-changes').click()
        page.get_by_test_id('plan-feedback-input').fill('Also check the ads.')
        page.get_by_test_id('plan-feedback-send').click()

        # A new planning round runs and comes back for approval.
        deadline = time.time() + 60
        while time.time() < deadline:
            s = api.get(f'/api/schedules/{schedule_id}').json()
            if (
                s['plan_status'] == 'pending_approval'
                and s['plan_version'] > first['plan_version']
            ):
                break
            time.sleep(0.5)
        assert s['plan_feedback'] == 'Also check the ads.'
        assert s['plan_version'] > first['plan_version']
        expect(page.get_by_test_id('plan-approve')).to_be_visible()
