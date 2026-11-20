"""Every task on a Ziniao store brings Ziniao up before its agent runs.

The user's spec: "所有自动任务的场景都改成自动重启，不要报错 … 只要建了任务跑
任何该 Kill 就 kill". An auto-mode task used to start its agent straight
away and leave Ziniao to the agent's first browser call, which then met a
cold (or normal-mode) client through a 90 s curl. Now the task itself
makes sure Ziniao answers — killing and relaunching it when needed — and
only a start that really fails fails the task, with a category the UI can
explain.
"""

import pytest

from app.browser.manager import BrowserManager
from app.browser.ziniao_startup import ZiniaoStartError
from tests.workflow.conftest import wait_for_task

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


async def test_an_auto_task_brings_ziniao_up_before_its_agent(
    admin_client, install_fake_agent, monkeypatch
):
    order: list[str] = []

    async def ready(self, store, db, **kw):
        order.append(f'ziniao:{store.name}')
        assert not install_fake_agent.calls, 'agent ran before Ziniao'

    monkeypatch.setattr(
        BrowserManager, 'ensure_ziniao_ready', ready, raising=False
    )
    store_id = await _ziniao_store(admin_client)
    r = await admin_client.post(
        '/api/tasks',
        json={'title': 'Read orders', 'plan_mode': False, 'store_id': store_id},
    )
    data = await wait_for_task(admin_client, r.json()['id'])
    assert data['status'] == 'completed'
    assert order == ['ziniao:Ziniao Store']
    assert len(install_fake_agent.calls) == 1


async def test_a_failed_start_fails_the_task_with_its_category(
    admin_client, install_fake_agent, monkeypatch
):
    async def ready(self, store, db, **kw):
        raise ZiniaoStartError(
            'Ziniao did not start: opened 2 times',
            'startup_timeout',
            'ziniao_start_timeout',
        )

    monkeypatch.setattr(
        BrowserManager, 'ensure_ziniao_ready', ready, raising=False
    )
    store_id = await _ziniao_store(admin_client)
    r = await admin_client.post(
        '/api/tasks',
        json={'title': 'Read orders', 'plan_mode': False, 'store_id': store_id},
    )
    data = await wait_for_task(admin_client, r.json()['id'], target='failed')
    assert data['error_category'] == 'ziniao_start_timeout'
    assert 'opened 2 times' in data['error']
    assert install_fake_agent.calls == []
