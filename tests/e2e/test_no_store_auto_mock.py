"""E2E (mock CLI): All-Stores tasks run Auto, and the spawn rules bite.

Runs in the e2e-mock-cli job — the mock CLI answers every task, so no
model credits. The server is owned by this module (dedicated port and
home), and it is a fresh install: no other task rows exist, so the
"total count" assertions are absolute.

* M1 (UI): in All Stores the dialog creates an AUTO task (POST body
  ``plan_mode: false``) and the footer's Auto/Review switch renders
  and works for it — the task completes with no plan card.
* M2 (API): a no-store auto task spawns its per-store children
  through the REAL in-task stdio MCP (``[[MOCK_SPAWN_SUBTASKS]]``,
  ai_bot token, no plan_mode in the request): exactly two children,
  each store-bound and Auto, the no-store spawn refused on the
  action, no extra rows.
* M3 (API): a CHILD running the same spawn (plus
  ``[[MOCK_TRY_GRANDCHILD]]``) is refused — depth stays 1.
* M4 (UI): with the user default Review, a no-store task parks at
  planned and the footer switch flips it to Auto.
"""

import logging
import os
import re
import time

import httpx
from playwright.sync_api import Page, expect
import pytest

from tests.e2e import e2e_helpers
from tests.e2e.e2e_helpers import create_store, create_task, login_client
from tests.e2e.own_server import owned_agent_stack

logger = logging.getLogger(__name__)

MOCK_CLI = os.environ.get('MOCK_CLI', '')
if not MOCK_CLI:
    pytest.skip(
        'MOCK_CLI not set — the no-store auto mock e2e needs the mock CLI',
        allow_module_level=True,
    )

pytestmark = [pytest.mark.e2e]

SPAWN = '[[MOCK_SPAWN_SUBTASKS]]'
GRANDCHILD = '[[MOCK_TRY_GRANDCHILD]]'

# Generous for a CI runner: the spawn flow additionally boots one
# stdio MCP subprocess per marked task.
TERMINAL_S = 120


@pytest.fixture(scope='module')
def auto_server(tmp_path_factory):
    with owned_agent_stack(
        tmp_path_factory,
        'no-store-auto-mock',
        env={'MOCK_CLI': MOCK_CLI},
    ) as server:
        yield server


@pytest.fixture
def api(auto_server):
    client = httpx.Client(timeout=60)
    login_client(client, auto_server.base_url)
    # Each test states its own execution-mode precondition; the
    # Review-default test flips it and must not leak that.
    client.patch(
        f'{auto_server.base_url}/api/auth/me/profile',
        json={'plan_mode_default': False},
    ).raise_for_status()
    yield client
    client.close()


def _url(auto_server, path: str) -> str:
    return auto_server.base_url.rstrip('/') + path


def _all_task_ids(client: httpx.Client, base_url: str) -> set[str]:
    tasks = client.get(f'{base_url}/api/tasks').json()
    return {t['id'] for t in tasks}


def _messages(client: httpx.Client, base_url: str, task_id: str) -> list[dict]:
    return client.get(f'{base_url}/api/tasks/{task_id}/messages').json()


def _result_row(client: httpx.Client, base_url: str, task_id: str) -> str:
    """The task's final result message (the mock echoes every tool
    result into it once, so one refused spawn = one refusal string)."""
    msgs = _messages(client, base_url, task_id)
    rows = [
        str(m.get('content') or '') for m in msgs if m.get('role') == 'result'
    ]
    assert rows, f'no result message for {task_id}'
    return rows[-1]


def _children(client: httpx.Client, base_url: str, parent_id: str) -> list:
    return client.get(f'{base_url}/api/tasks?parent_task_id={parent_id}').json()


class TestM1UiAllStoresAuto:
    def test_all_stores_creates_auto_and_footer_switch_works(
        self, auto_server, authenticated_page: Page
    ):
        page = authenticated_page
        assert e2e_helpers.BASE_URL == auto_server.base_url

        page.locator('button', has_text='All Stores').first.click()
        page.get_by_role('button', name='+ New Task').click()
        prompt = f'all-stores auto {int(time.time())}'
        page.get_by_placeholder('e.g. Process billing files').fill(prompt)
        with page.expect_response(
            lambda r: r.url.endswith('/api/tasks')
            and r.request.method == 'POST'
        ) as created:
            page.get_by_role('button', name='Create', exact=True).click()
        body = created.value.request.post_data_json
        assert body.get('plan_mode') is False, body
        task = created.value.json()
        assert task['plan_mode'] is False
        assert task['store_id'] is None
        task_id = task['id']

        # The footer's Auto/Review switch renders ENABLED for a
        # no-store task (it used to be a dead 'Review plan first'
        # label) — and Auto is pressed.
        auto_btn = page.get_by_role(
            'button', name=re.compile('^auto$', re.I)
        ).first
        expect(auto_btn).to_be_visible()
        expect(auto_btn).to_have_attribute('aria-pressed', 'true')
        expect(
            page.get_by_role('button', name=re.compile('^review$', re.I)).first
        ).to_be_visible()

        deadline = time.time() + TERMINAL_S
        while time.time() < deadline:
            data = page.request.get(
                _url(auto_server, f'/api/tasks/{task_id}')
            ).json()
            if data['status'] in ('completed', 'failed'):
                break
            time.sleep(1)
        assert data['status'] == 'completed', data['status']
        assert data['plan'] is None  # never entered the plan flow


class TestM2OrchestratorSpawns:
    def test_no_store_auto_task_spawns_store_children(self, auto_server, api):
        base = auto_server.base_url
        sa = create_store(api, f'nostore-a-{int(time.time())}')['id']
        sb = create_store(api, f'nostore-b-{int(time.time())}')['id']

        before = _all_task_ids(api, base)
        task = create_task(
            api,
            'Split per store',
            description=f'{SPAWN} stores: {sa},{sb}',
            profile_id=e2e_helpers.DEFAULT_PROFILE_ID,
        )
        parent_id = task['id']
        assert task['plan_mode'] is False

        parent = e2e_helpers.poll_task_status(
            api,
            parent_id,
            {'completed'},
            fail_statuses={'failed'},
            timeout=TERMINAL_S,
        )
        assert parent['status'] == 'completed', parent.get('error')

        kids = _children(api, base, parent_id)
        assert {k['store_id'] for k in kids} == {sa, sb}
        assert len(kids) == 2
        for k in kids:
            # Spawned through the in-task MCP with NO plan_mode: the
            # sub-task rule makes it Auto.
            assert k['plan_mode'] is False, k
            assert k['status'] in ('completed', 'failed'), k['status']

        # The no-store spawn was refused on the action; the mock
        # echoes every tool result into its transcript. (The parent's
        # `result` FIELD is the child-aggregation summary once
        # children finish — see _aggregate_child_results — so the
        # refusal text is asserted in the agent's own result row,
        # exactly once for the one attempt.)
        echoed = _result_row(api, base, parent_id)
        assert echoed.count('must carry a store_id') == 1, echoed[-800:]

        # Total rows: exactly parent + 2 children (the refused spawn
        # left nothing behind).
        created = _all_task_ids(api, base) - before
        assert len(created) == 3, created


class TestM3DepthCapped:
    def test_child_cannot_spawn(self, auto_server, api):
        base = auto_server.base_url
        sa = create_store(api, f'nostore-c-{int(time.time())}')['id']
        parent = create_task(
            api,
            'depth parent',
            plan_mode=False,
            profile_id=e2e_helpers.DEFAULT_PROFILE_ID,
        )
        e2e_helpers.poll_task_status(
            api, parent['id'], {'completed'}, timeout=TERMINAL_S
        )

        before = _all_task_ids(api, base)
        # A CHILD that behaves like an orchestrator: every spawn it
        # attempts is a grandchild, and must be refused.
        child = api.post(
            f'{base}/api/tasks',
            json={
                'title': 'child that tries to spawn',
                'store_id': sa,
                'parent_task_id': parent['id'],
                'description': f'{SPAWN} {GRANDCHILD} stores: {sa}',
                'plan_mode': False,
                'profile_id': e2e_helpers.DEFAULT_PROFILE_ID,
            },
        )
        assert child.status_code == 200, child.text
        child_id = child.json()['id']

        done = e2e_helpers.poll_task_status(
            api,
            child_id,
            {'completed'},
            fail_statuses={'failed'},
            timeout=TERMINAL_S,
        )
        assert done['status'] == 'completed', done.get('error')

        # One result row = the mock's single echo of all tool results.
        # This child attempts TWO depth-refused spawns (the per-store
        # spawn and the explicit nested one) and one store-refused
        # spawn — each refused exactly once, in a single round (an
        # unmet retry would raise these counts).
        echoed = _result_row(api, base, child_id)
        assert echoed.count('sub-task cannot create sub-tasks') == 2, echoed[
            -800:
        ]
        assert echoed.count('must carry a store_id') == 1, echoed[-800:]

        # Depth stays 1: no row was created under the child.
        assert _children(api, base, child_id) == []
        assert (_all_task_ids(api, base) - before) == {child_id}


class TestM4ReviewDefault:
    def test_review_default_parks_then_switch_to_auto(
        self, auto_server, authenticated_page: Page
    ):
        page = authenticated_page
        page.request.patch(
            _url(auto_server, '/api/auth/me/profile'),
            data={'plan_mode_default': True},
        )
        page.reload()
        try:
            page.locator('button', has_text='All Stores').first.click()
            page.get_by_role('button', name='+ New Task').click()
            prompt = f'all-stores review {int(time.time())}'
            page.get_by_placeholder('e.g. Process billing files').fill(prompt)
            with page.expect_response(
                lambda r: r.url.endswith('/api/tasks')
                and r.request.method == 'POST'
            ) as created:
                page.get_by_role('button', name='Create', exact=True).click()
            body = created.value.request.post_data_json
            assert body.get('plan_mode') is True, body
            task_id = created.value.json()['id']

            # Parks at planned (the mock writes a plan and waits).
            deadline = time.time() + TERMINAL_S
            data: dict = {}
            while time.time() < deadline:
                data = page.request.get(
                    _url(auto_server, f'/api/tasks/{task_id}')
                ).json()
                if data['status'] in ('planned', 'completed', 'failed'):
                    break
                time.sleep(1)
            assert data['status'] == 'planned', data['status']

            # The footer switch shows REVIEW pressed; clicking Auto
            # PATCHes this task's mode.
            review_btn = page.get_by_role(
                'button', name=re.compile('^review$', re.I)
            ).first
            expect(review_btn).to_have_attribute('aria-pressed', 'true')
            with page.expect_response(
                lambda r: '/review-plan' in r.url
                and r.request.method == 'PATCH'
            ) as switched:
                page.get_by_role(
                    'button', name=re.compile('^auto$', re.I)
                ).first.click()
            assert switched.value.status == 200, switched.value.text()
            expect(
                page.get_by_role(
                    'button', name=re.compile('^auto$', re.I)
                ).first
            ).to_have_attribute('aria-pressed', 'true')
            got = page.request.get(
                _url(auto_server, f'/api/tasks/{task_id}')
            ).json()
            assert got['plan_mode'] is False
        finally:
            page.request.post(
                _url(auto_server, f'/api/tasks/{task_id}/agent/stop')
            )
            page.request.patch(
                _url(auto_server, '/api/auth/me/profile'),
                data={'plan_mode_default': False},
            )
