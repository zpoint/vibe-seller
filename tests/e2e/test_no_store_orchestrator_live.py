"""Live (real LLM): an All-Stores AUTO task delegates per store.

Pins with a real model: a no-store task an Auto user creates runs
straight through (plan_mode False, never planning), hands each store's
seller-center work to a store-bound sub-task, nothing spawns deeper,
and the parent completes once its sub-tasks are terminal.

The ask is seller-center work because an All-Stores task cannot reach
a store's seller center itself — delegating it is the product's own
rule, not a phrasing the prompt dictates.

Skips when MOCK_CLI is set (that lane lives in
test_no_store_auto_mock.py) or the server reports no AI ready. Like
every live test it runs in the gated e2e-test job and spends provider
credits.
"""

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


def _wait_parent(live_api: httpx.Client, task_id: str) -> dict:
    """Poll to a terminal state, failing the moment a plan parks.

    An AUTO task must never enter the planning states; waiting for
    that observation is the assertion, so a poll that only accepts
    terminal statuses (e2e_helpers.poll_task_status) would be blind
    here — it would let a planned park ride out to timeout.
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
        if status in TERMINAL:
            return data
        time.sleep(2)
    raise TimeoutError(
        f'task {task_id[:8]} not terminal within {PIPELINE_TIMEOUT}s '
        f'(last={data.get("status")})'
    )


def test_all_stores_auto_task_runs_and_keeps_the_tree_flat(
    live_api: httpx.Client,
):
    tag = run_tag()
    name_a, name_b = f'live-orch-a-{tag}', f'live-orch-b-{tag}'
    sa = create_store(live_api, name_a)['id']
    sb = create_store(live_api, name_b)['id']
    # Pure human intent, no mechanics, no tool names. The stores are
    # NAMED: on the shared CI server "each of my stores" would mean
    # every store the other workers created. These stores have no
    # login, so a sub-task may end failed — the parent still completes
    # once every sub-task is terminal, which is what is pinned.
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

        # The parent does not hang: it completes by aggregation once
        # every sub-task is terminal.
        final = _wait_parent(live_api, parent_id)
        assert final['status'] == 'completed', final

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
            final['status'],
            len(children),
            sorted({str(c['store_id'])[:8] for c in children}),
        )
    finally:
        live_api.patch(
            f'{BASE_URL}/api/auth/me/profile',
            json={'plan_mode_default': original},
        )
