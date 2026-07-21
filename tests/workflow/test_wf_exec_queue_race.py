"""Exec-path RUNNING is committed only after the concurrency slot.

Observed live: an all-stores schedule fanned out more tasks than
``max_concurrent``; ``execute_planned_task`` set RUNNING *before*
``agent_manager.run()`` blocked on the semaphore, so the queued tasks
sat RUNNING-and-silent with ``updated_at`` frozen at creation — the
stall reaper failed them as "stalled" within 3 minutes, and when a
slot finally freed the queued run started a full agent session against
the already-FAILED task (burning a complete audit into a dead task).

Contract (mirrors ``auto_run_task._on_start``):
- a task queued for the semaphore keeps its pre-run status;
- the RUNNING transition (+ ``updated_at`` bump) happens only after
  the slot is acquired;
- a task that left the expected state while queued aborts the spawn.
"""

import asyncio

import pytest

from app.models.task import Task
from tests.workflow.conftest import wait_for_task
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow


async def _create_store(client, name):
    r = await client.post('/api/stores', json={'name': name})
    return r.json()['id']


async def _planned_task_with_dead_session(
    admin_client, install_fake_agent, store_id, title
):
    """A PLANNED task whose planning session is gone — the
    execute-plan path must spawn a fresh session (the branch that
    queues on the semaphore)."""
    r = await admin_client.post(
        '/api/tasks',
        json={'title': title, 'store_id': store_id, 'plan_mode': True},
    )
    task_id = r.json()['id']
    await wait_for_task(admin_client, task_id, target='planned')
    # Simulate the planning session having died (server restart /
    # reaped CLI): stop() cancels the fake session AND releases its
    # concurrency slot, so approve_plan() returns False and
    # execute-plan takes the fresh-spawn branch.
    await install_fake_agent.stop(task_id)
    return task_id


class TestQueuedExecKeepsPreRunStatus:
    async def test_queued_task_stays_planned_then_completes(
        self, admin_client, install_fake_agent
    ):
        install_fake_agent.set_max_concurrent(1)
        store_id = await _create_store(admin_client, 'Queue Race Store')

        # Task B first (the semaphore also gates planning sessions):
        # planned, then its planning session dies → execute-plan must
        # spawn fresh.
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan='## Plan\n1. do the thing', result='B done'
        )
        b_id = await _planned_task_with_dead_session(
            admin_client, install_fake_agent, store_id, 'queued executee'
        )

        # Task A occupies the single slot until we release its gate.
        # (The scenario is read when run() starts, so set the gated
        # default BEFORE creating A, then restore B's scenario.)
        gate = asyncio.Event()
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='holder done', gate=gate
        )
        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'slot holder', 'store_id': store_id},
        )
        holder_id = r.json()['id']
        for _ in range(200):
            if install_fake_agent.is_running(holder_id):
                break
            await asyncio.sleep(0.02)
        assert install_fake_agent.is_running(holder_id)
        install_fake_agent.default_scenario = FakeAgentScenario(result='B done')

        pre = (await admin_client.get(f'/api/tasks/{b_id}')).json()
        r2 = await admin_client.post(f'/api/tasks/{b_id}/execute-plan')
        assert r2.status_code == 200, (r2.text, pre['status'], pre.get('plan'))

        # While queued for the semaphore, B must NOT be RUNNING — the
        # exact state the stall reaper used to kill.
        await asyncio.sleep(0.3)
        data = (await admin_client.get(f'/api/tasks/{b_id}')).json()
        assert data['status'] in ('planned', 'queued')

        # Release the slot: B transitions RUNNING only now, executes,
        # completes.
        gate.set()
        final = await wait_for_task(admin_client, b_id)
        assert final['status'] == 'completed'
        assert final['result'] == 'B done'

    async def test_task_failed_while_queued_aborts_spawn(
        self, admin_client, install_fake_agent, override_async_session
    ):
        install_fake_agent.set_max_concurrent(1)
        store_id = await _create_store(admin_client, 'Queue Abort Store')

        install_fake_agent.default_scenario = FakeAgentScenario(
            plan='## Plan\n1. do the thing', result='must never appear'
        )
        b_id = await _planned_task_with_dead_session(
            admin_client, install_fake_agent, store_id, 'doomed executee'
        )

        gate = asyncio.Event()
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='holder done', gate=gate
        )
        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'slot holder 2', 'store_id': store_id},
        )
        holder_id = r.json()['id']
        for _ in range(200):
            if install_fake_agent.is_running(holder_id):
                break
            await asyncio.sleep(0.02)
        assert install_fake_agent.is_running(holder_id)
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='must never appear'
        )

        r2 = await admin_client.post(f'/api/tasks/{b_id}/execute-plan')
        assert r2.status_code == 200
        await asyncio.sleep(0.2)

        # Reaper/user kills the task while it waits for a slot.
        async with override_async_session() as db:
            t = await db.get(Task, b_id)
            t.status = 'failed'
            t.error = 'force-failed while queued'
            await db.commit()

        gate.set()
        # The queued spawn must ABORT (on_start sees FAILED): no agent
        # run, no result, status untouched.
        await asyncio.sleep(0.5)
        data = (await admin_client.get(f'/api/tasks/{b_id}')).json()
        assert data['status'] == 'failed'
        assert not data.get('result')
        runs = install_fake_agent.get_calls(task_id=b_id, action='run')
        # Only the original planning run — the queued EXECUTE spawn
        # was aborted by on_start.
        assert len(runs) == 1
        assert runs[0].mode == 'plan_then_execute'
