"""Live (real LLM): an All-Stores AUTO task delegates per store.

Pins with a real model: a no-store task an Auto user creates runs
straight through (plan_mode False, never planning), hands each store's
seller-center work to a store-bound Auto sub-task, nothing spawns
deeper, and the parent ENDS ITS TURN into the sub-task wait — it does
not hold its session (and a run slot) polling for them.

The ask is seller-center work because an All-Stores task cannot reach
a store's seller center itself — delegating it is the product's own
rule, not a phrasing the prompt dictates.

What the sub-tasks then do on these login-less stores is model-paced
and not this test's subject; the tree is stopped once the shape is
pinned. That the parent completes by itself when its sub-tasks finish
is a server rule, pinned deterministically in test_no_store_auto_mock
(M2) and tests/workflow/test_wf_no_store_auto.py (W4).

Skips when MOCK_CLI is set (that lane lives in
test_no_store_auto_mock.py) or the server reports no AI ready. Like
every live test it runs in the gated e2e-test job and spends provider
credits.
"""

import json
import logging
import os
import time

import httpx
import pytest

from tests.e2e.conftest import BASE_URL
from tests.e2e.e2e_helpers import (
    PIPELINE_TIMEOUT,
    create_store,
    create_task,
    run_tag,
)

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.e2e]

PLAN_STATES = {'planned', 'designing'}
TERMINAL = {'completed', 'failed'}


@pytest.fixture(scope='module')
def live_api(api_client: httpx.Client) -> httpx.Client:
    """The shared api_client, gated on a REAL agent lane being possible."""
    if os.environ.get('MOCK_CLI', ''):
        pytest.skip(
            'MOCK_CLI server — delegation lives in '
            'test_no_store_auto_mock.py; this file needs a real model'
        )
    ready = api_client.get(f'{BASE_URL}/api/profiles/readiness').json()
    if not ready.get('ready'):
        pytest.skip(f'server reports no AI ready: {ready}')
    return api_client


def _waits_on_children(task: dict) -> bool:
    try:
        cond = json.loads(task.get('wait_condition') or '{}')
    except (TypeError, ValueError):
        return False
    return task.get('status') == 'waiting' and cond.get('strategy') == (
        'children'
    )


def _wait_handed_off(live_api: httpx.Client, task_id: str) -> dict:
    """Poll until the parent's turn is over, failing if a plan parks.

    Over means: waiting on its sub-tasks, or terminal. An AUTO task must
    never enter the planning states; a parent still RUNNING at the
    deadline kept its session open after delegating.
    """
    deadline = time.time() + PIPELINE_TIMEOUT
    data: dict = {}
    while time.time() < deadline:
        data = live_api.get(f'{BASE_URL}/api/tasks/{task_id}').json()
        status = data.get('status')
        if status in PLAN_STATES:
            raise AssertionError(
                f'an auto no-store task entered {status!r} — the mode '
                f'was not honored (task {task_id[:8]})'
            )
        if status in TERMINAL or _waits_on_children(data):
            return data
        time.sleep(2)
    raise TimeoutError(
        f'task {task_id[:8]} did not hand off within {PIPELINE_TIMEOUT}s '
        f'(last={data.get("status")})'
    )


def _stop(live_api: httpx.Client, task_ids) -> None:
    for tid in task_ids:
        live_api.post(f'{BASE_URL}/api/tasks/{tid}/agent/stop')


def test_all_stores_auto_task_delegates_and_hands_off(
    live_api: httpx.Client,
):
    tag = run_tag()
    name_a, name_b = f'live-orch-a-{tag}', f'live-orch-b-{tag}'
    sa = create_store(live_api, name_a)['id']
    sb = create_store(live_api, name_b)['id']
    # Pure human intent, no mechanics, no tool names. The stores are
    # NAMED: on the shared CI server "each of my stores" would mean
    # every store the other workers created.
    prompt = (
        f'Check the seller center of my stores {name_a} and {name_b} '
        'and tell me how many open orders each one has.'
    )

    # Own precondition: the user's DEFAULT is Auto; the profile is the
    # signed-in user's default_profile_id — exactly what the UI sends.
    me = live_api.get(f'{BASE_URL}/api/auth/me').json()
    original = me['plan_mode_default']
    live_api.patch(
        f'{BASE_URL}/api/auth/me/profile',
        json={'plan_mode_default': False},
    ).raise_for_status()
    parent_id = None
    try:
        task = create_task(
            live_api,
            'Open orders in each store',
            description=prompt,
            profile_id=me['default_profile_id'],
        )
        parent_id = task['id']
        assert task['store_id'] is None
        assert task['plan_mode'] is False, 'auto default must be honored'

        handed = _wait_handed_off(live_api, parent_id)

        tasks = live_api.get(f'{BASE_URL}/api/tasks').json()
        children = [t for t in tasks if t.get('parent_task_id') == parent_id]

        # Delegated per store; every sub-task store-bound, auto (spawned
        # with no explicit mode), and nothing deeper than one level.
        assert {sa, sb} <= {c['store_id'] for c in children}, children
        assert all(c['store_id'] for c in children), children
        assert all(c['plan_mode'] is False for c in children), children
        child_ids = {c['id'] for c in children}
        assert not [t for t in tasks if t.get('parent_task_id') in child_ids], (
            'a sub-task spawned sub-tasks'
        )
        # Only the parent is store-less IN THIS TEST'S LINEAGE (the
        # shared CI server carries other workers' tasks).
        lineage = {parent_id} | child_ids
        assert [
            t['id']
            for t in tasks
            if t['id'] in lineage and t['store_id'] is None
        ] == [parent_id]
        logger.info(
            'live no-store auto run %s: parent=%s children=%d (%s)',
            parent_id[:8],
            handed['status'],
            len(children),
            sorted({str(c['store_id'])[:8] for c in children}),
        )
    finally:
        # Free the run slots: the sub-tasks' work on login-less stores
        # is not this test's subject.
        if parent_id:
            kids = live_api.get(
                f'{BASE_URL}/api/tasks', params={'parent_task_id': parent_id}
            ).json()
            _stop(live_api, [k['id'] for k in kids] + [parent_id])
        live_api.patch(
            f'{BASE_URL}/api/auth/me/profile',
            json={'plan_mode_default': original},
        )
