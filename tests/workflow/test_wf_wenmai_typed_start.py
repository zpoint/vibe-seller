"""A 稳卖 LOGIN_REQUIRED reaches C2's typed start contract (W6).

The REAL WenmaiBackend against a logged-out fake API (a store with no
stored credentials) raises the REAL ``LoginRequired`` — the route must
answer C2's typed body (``code`` / ``message`` / ``hint`` at 500) and
the task must end FAILED with ``error_category='login_required'`` and
the agent stopped. #333 raises the taxonomy error; #342 owns the typed
body — this test pins that they compose.
"""

import asyncio
import json

import pytest

from app.browser.adapters.wenmai import WenmaiBackend
from tests.unit.test_browser.fake_wenmai_api import FakeWenmaiApi
from tests.workflow.conftest import wait_for_task
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow


@pytest.fixture
async def logged_out_wenmai_api():
    """A fake 稳卖 API answering every relayed method logged-out."""
    api = FakeWenmaiApi()
    api.logged_out = True
    await api.start()
    try:
        yield api
    finally:
        await api.stop()


@pytest.fixture
def real_wenmai_start(mock_browser_wf, logged_out_wenmai_api):
    """The mocked manager runs the REAL wenmai backend against the
    logged-out fake API — the exception the route sees is the one the
    real adapter raises, not a stand-in. Returns the API port."""

    async def _start_session(store, db):
        cfg = json.loads(store.browser_config or '{}')
        backend = WenmaiBackend()
        await backend.start(cfg)

    mock_browser_wf.start_session = _start_session
    return logged_out_wenmai_api.port


async def _make_wenmai_store(client, api_port: int) -> str:
    r = await client.post(
        '/api/stores',
        json={
            'name': '示例甲1店',
            'browser_backend': 'wenmai',
            'browser_oauth': '100000000001',
            'browser_config': {
                'api_port': api_port,
                'browser_ref': '100000000001',
                # No wenmai_company/username/password: the logged-out
                # client must raise LOGIN_REQUIRED (no stored
                # credentials → terminal per the #342 contract).
            },
        },
    )
    assert r.status_code == 200, r.text
    return r.json()['id']


async def _make_running_task(client, install_fake_agent, store_id: str):
    gate = asyncio.Event()
    install_fake_agent.default_scenario = FakeAgentScenario(
        result='done after gate', gate=gate
    )
    r = await client.post(
        '/api/tasks',
        json={'title': 'open the store backend', 'store_id': store_id},
    )
    assert r.status_code == 200, r.text
    task_id = r.json()['id']
    for _ in range(200):
        data = (await client.get(f'/api/tasks/{task_id}')).json()
        if data['status'] == 'running':
            return task_id, gate
        await asyncio.sleep(0.02)
    raise AssertionError('task never reached running')


async def test_wenmai_login_required_reaches_the_typed_contract(
    admin_client, install_fake_agent, real_wenmai_start
):
    """The real adapter's LOGIN_REQUIRED → C2's typed body + the task
    fails with its error_category and the agent stops."""
    store_id = await _make_wenmai_store(admin_client, real_wenmai_start)
    task_id, gate = await _make_running_task(
        admin_client, install_fake_agent, store_id
    )

    r = await admin_client.post(
        f'/api/stores/{store_id}/browser/start',
        headers={'X-Vibe-Task': task_id},
    )
    # C2's typed body: the stable code, the message, the hint.
    assert r.status_code == 500
    detail = r.json()['detail']
    assert detail['code'] == 'LOGIN_REQUIRED'
    assert detail['hint']

    # The task ended FAILED with the typed error + category.
    data = await wait_for_task(admin_client, task_id, 'failed')
    assert data['error'].startswith('[LOGIN_REQUIRED]')
    assert data['error_category'] == 'login_required'

    # The agent was stopped: the gate never opened and no session is
    # registered for the task.
    assert not gate.is_set()
    assert install_fake_agent.get_session(task_id) is None
