"""Changing max agent concurrency in Settings takes effect on the queue.

Observed live: max_agent_concurrency was raised from 2 to 5 while two
fan-out tasks were queued for an agent slot; they stayed ``queued``
forever, because the resize swapped in a new semaphore and the queued
launches were still waiting on the old one.

Driven through the real API (PUT /api/settings, POST /api/tasks) with
the fake agent, which shares the production slot limiter:
- raising the limit starts the already-queued tasks at once;
- lowering it lets running tasks finish and holds new ones until
  in-flight drops below the new limit.
"""

import asyncio

import pytest

from tests.workflow.conftest import real_sleep, wait_for_task
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow

# Statuses of a task waiting for an agent slot (not yet RUNNING).
_WAITING = ('pending', 'queued')


async def _set_limit(client, n):
    r = await client.put(
        '/api/settings', json={'max_agent_concurrency': str(n)}
    )
    assert r.status_code == 200, r.text


async def _create_task(client, store_id, title):
    r = await client.post(
        '/api/tasks', json={'title': title, 'store_id': store_id}
    )
    assert r.status_code in (200, 201), r.text
    return r.json()['id']


async def _status(client, task_id):
    return (await client.get(f'/api/tasks/{task_id}')).json()['status']


async def _wait_running(fake, task_ids, timeout=5.0):
    for _ in range(int(timeout / 0.02)):
        if all(fake.is_running(t) for t in task_ids):
            return
        await real_sleep(0.02)
    raise AssertionError(
        f'not running: {[t for t in task_ids if not fake.is_running(t)]}'
    )


async def test_raising_the_limit_starts_queued_tasks(
    admin_client, install_fake_agent
):
    fake = install_fake_agent
    r = await admin_client.post('/api/stores', json={'name': 'Resize Up'})
    store_id = r.json()['id']
    await _set_limit(admin_client, 1)

    gate = asyncio.Event()
    fake.default_scenario = FakeAgentScenario(result='done', gate=gate)
    ids = [
        await _create_task(admin_client, store_id, f'reply OK {i}')
        for i in range(4)
    ]
    await real_sleep(0.3)
    running = [t for t in ids if fake.is_running(t)]
    assert len(running) == 1
    queued = [t for t in ids if t not in running]
    for t in queued:
        assert await _status(admin_client, t) in _WAITING

    await _set_limit(admin_client, 4)

    # The queued tasks start with the first one still holding its slot.
    await _wait_running(fake, ids)
    for t in queued:
        assert await _status(admin_client, t) == 'running'

    gate.set()
    for t in ids:
        assert (await wait_for_task(admin_client, t))['status'] == ('completed')


async def test_lowering_the_limit_caps_new_starts(
    admin_client, install_fake_agent
):
    fake = install_fake_agent
    r = await admin_client.post('/api/stores', json={'name': 'Resize Down'})
    store_id = r.json()['id']
    await _set_limit(admin_client, 4)

    gates = {}
    task_of = {}
    for name in ('a', 'b', 'c'):
        gates[name] = asyncio.Event()
        fake.default_scenario = FakeAgentScenario(
            result=f'{name} done', gate=gates[name]
        )
        tid = await _create_task(admin_client, store_id, f'reply OK {name}')
        await _wait_running(fake, [tid])
        task_of[name] = tid

    await _set_limit(admin_client, 1)
    fake.default_scenario = FakeAgentScenario(result='late done')
    late = await _create_task(admin_client, store_id, 'reply OK late')

    await real_sleep(0.3)
    # Running agents keep going; the new task waits.
    await _wait_running(fake, list(task_of.values()))
    assert await _status(admin_client, late) in _WAITING

    gates['a'].set()
    gates['b'].set()
    for name in ('a', 'b'):
        await wait_for_task(admin_client, task_of[name])
    await real_sleep(0.3)
    # One still runs and the limit is one: no new start yet.
    assert await _status(admin_client, late) in _WAITING
    assert not fake.is_running(late)

    gates['c'].set()
    await wait_for_task(admin_client, task_of['c'])
    final = await wait_for_task(admin_client, late)
    assert final['status'] == 'completed'
    assert final['result'] == 'late done'
