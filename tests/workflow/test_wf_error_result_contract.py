"""The set_task_error / set_task_result verdict contract.

``set_task_error`` means UNRECOVERABLE failure. Observed live: an
agent under review-gate pressure produced a complete deliverable, then
called ``set_task_error`` with a "remaining caveats" note to be
allowed to end — and the non-empty ``task.error`` flipped a task with
a valid 100KB-class result to FAILED, hiding the deliverable behind a
failure badge. Two contract rules pin the fix:

- error-after-result → recorded as a CAVEAT appended to the result;
  ``task.error`` untouched; the task completes.
- result-after-error → the valid result supersedes the earlier
  agent-reported error (recovery); infra-detected errors are never
  cleared this way.
"""

import asyncio

import pytest

from tests.workflow.conftest import wait_for_task
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow


async def _create_store(client, name):
    r = await client.post('/api/stores', json={'name': name})
    return r.json()['id']


async def _running_task(admin_client, install_fake_agent, store_name):
    """A task whose fake agent stays alive (gate) so the MCP endpoints
    see status=RUNNING, plus its release gate."""
    gate = asyncio.Event()
    install_fake_agent.default_scenario = FakeAgentScenario(
        result='deliverable from the agent',
        gate=gate,
    )
    store_id = await _create_store(admin_client, store_name)
    r = await admin_client.post(
        '/api/tasks',
        json={'title': 'contract test', 'store_id': store_id},
    )
    task_id = r.json()['id']
    for _ in range(200):
        data = (await admin_client.get(f'/api/tasks/{task_id}')).json()
        if data['status'] == 'running':
            break
        await asyncio.sleep(0.02)
    assert data['status'] == 'running'
    return task_id, gate


class TestErrorAfterResultIsCaveat:
    async def test_error_with_existing_result_becomes_caveat(
        self, admin_client, install_fake_agent
    ):
        task_id, gate = await _running_task(
            admin_client, install_fake_agent, 'Caveat Contract Store'
        )
        r = await admin_client.post(
            f'/api/tasks/{task_id}/result',
            json={'result': 'Full audit report: 10/10 items covered.'},
        )
        assert r.status_code == 200

        r2 = await admin_client.post(
            f'/api/tasks/{task_id}/error',
            json={'error': '2 minor rows deferred to the next run.'},
        )
        assert r2.status_code == 200
        body = r2.json()
        assert body['recorded_as'] == 'caveat'

        data = (await admin_client.get(f'/api/tasks/{task_id}')).json()
        assert not data.get('error')
        assert 'Full audit report' in data['result']
        assert 'Agent-reported caveats' in data['result']
        assert '2 minor rows deferred' in data['result']

        gate.set()
        final = await wait_for_task(admin_client, task_id)
        assert final['status'] == 'completed'
        assert not final.get('error')

    async def test_error_without_result_still_fails_task(
        self, admin_client, install_fake_agent
    ):
        # The classic contract is untouched: no deliverable + error
        # → FAILED at cleanup.
        task_id, gate = await _running_task(
            admin_client, install_fake_agent, 'Hard Error Store'
        )
        r = await admin_client.post(
            f'/api/tasks/{task_id}/error',
            json={'error': 'service unreachable, no data collected'},
        )
        assert r.status_code == 200 and 'recorded_as' not in r.json()

        data = (await admin_client.get(f'/api/tasks/{task_id}')).json()
        assert 'service unreachable' in (data.get('error') or '')

        gate.set()
        final = await wait_for_task(admin_client, task_id, target='failed')
        assert final['status'] == 'failed'


class TestResultAfterErrorRecovers:
    async def test_result_clears_agent_reported_error(
        self, admin_client, install_fake_agent
    ):
        task_id, gate = await _running_task(
            admin_client, install_fake_agent, 'Recovery Contract Store'
        )
        r = await admin_client.post(
            f'/api/tasks/{task_id}/error',
            json={'error': 'first attempt hit a login wall'},
        )
        assert r.status_code == 200

        r2 = await admin_client.post(
            f'/api/tasks/{task_id}/result',
            json={'result': 'Recovered on retry: report complete.'},
        )
        assert r2.status_code == 200

        data = (await admin_client.get(f'/api/tasks/{task_id}')).json()
        assert not data.get('error')
        assert 'Recovered on retry' in data['result']

        gate.set()
        final = await wait_for_task(admin_client, task_id)
        assert final['status'] == 'completed'
