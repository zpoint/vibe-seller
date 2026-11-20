"""A task only touches Ziniao when it actually uses the store's browser.

The user's spec: Ziniao is closed and relaunched only when a task really
needs the browser — not at task start. A task on a Ziniao store that never
opens the browser (answers from notes, reads files, plans) must leave a
Ziniao the user opened by hand alone. Bringing Ziniao up is the job of the
browser start (``POST /api/stores/{id}/browser/start``, which the store's
browser-use wrapper calls on first use) — see test_stores.TestBrowserStart.
"""

import pytest

from app.browser.manager import BrowserManager
from tests.workflow.conftest import wait_for_task
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow


async def _ziniao_store(client) -> str:
    r = await client.post(
        '/api/ziniao-accounts',
        json={
            'name': 'z',
            'company': 'acme',
            'username': 'ops',
            'password': 'pw',
        },
    )
    assert r.status_code == 200, r.text
    r = await client.post(
        '/api/stores',
        json={
            'name': 'Ziniao Store',
            'browser_backend': 'ziniao',
            'ziniao_account_id': r.json()['id'],
            'browser_oauth': 'oauth-1',
        },
    )
    assert r.status_code == 200, r.text
    return r.json()['id']


@pytest.fixture
def ziniao_calls(monkeypatch):
    """Every way a task could reach the Ziniao client, recorded."""
    calls: list[str] = []

    async def record(name, *a, **kw):
        calls.append(name)

    for name in ('ensure_ziniao_ready', 'check_ziniao_reachable'):
        monkeypatch.setattr(
            BrowserManager,
            name,
            lambda self, *a, _n=name, **kw: record(_n),
            raising=False,
        )

    async def ensure(*a, **kw):
        calls.append('ensure_ziniao_running')

    monkeypatch.setattr(
        'app.browser.ziniao_utils.ensure_ziniao_running', ensure
    )
    return calls


async def test_an_auto_task_that_never_opens_the_browser_leaves_ziniao_alone(
    admin_client, install_fake_agent, ziniao_calls
):
    store_id = await _ziniao_store(admin_client)
    r = await admin_client.post(
        '/api/tasks',
        json={
            'title': 'Summarise notes',
            'plan_mode': False,
            'store_id': store_id,
        },
    )
    data = await wait_for_task(admin_client, r.json()['id'])
    assert data['status'] == 'completed'
    assert len(install_fake_agent.calls) == 1
    assert ziniao_calls == []


async def test_planning_and_executing_leave_ziniao_alone(
    admin_client, install_fake_agent, ziniao_calls
):
    install_fake_agent.default_scenario = FakeAgentScenario(
        plan='## Plan\nRead the notes', result='done'
    )
    store_id = await _ziniao_store(admin_client)
    r = await admin_client.post(
        '/api/tasks',
        json={
            'title': 'Plan then run',
            'plan_mode': True,
            'store_id': store_id,
        },
    )
    task_id = r.json()['id']
    await wait_for_task(admin_client, task_id, target='planned')
    await admin_client.post(f'/api/tasks/{task_id}/execute-plan')
    data = await wait_for_task(admin_client, task_id, target='completed')
    assert data['result'] == 'done'
    assert ziniao_calls == []
