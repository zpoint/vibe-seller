"""A new turn, and the terminal routes, drop the turn hold's re-drive ledger.

``turn_hold_budget._ledgers`` is keyed by task and outlives a session, so
it is bounded only because the terminal points drop it: the result being
persisted (``POST /api/tasks/{id}/result``) and the task being deleted.
Unit tests pin ``reset_ledger`` itself; only these catch a missing CALL
SITE — resetting at turn entry alone leaves one entry per task forever,
and a later turn on the same task would inherit the old spend.
"""

import asyncio
from datetime import UTC, datetime

import pytest

from app.ai.turn_hold_budget import _ledgers, ledger_for  # noqa: PLC2701
from app.models.task import Task
import app.routers.tasks as tasks_router
from app.task_states import TaskStatus
from tests.workflow.conftest import wait_for_task
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow


async def _seed_running_task(db_maker) -> str:
    async with db_maker() as db:
        now = datetime.now(UTC).isoformat()
        task = Task(
            title='ledger',
            created_by='00000000-0000-0000-0000-000000000001',
            status=TaskStatus.RUNNING,
            priority=1,
            plan_mode=False,
            created_at=now,
            updated_at=now,
        )
        db.add(task)
        await db.commit()
        return task.id


def _spend(task_id: str) -> None:
    """What a session does on its first re-drive: allocate the ledger."""
    ledger_for(task_id, 300.0).note_redrive()
    assert task_id in _ledgers


async def test_set_result_drops_the_redrive_ledger(
    admin_client, override_async_session, monkeypatch, tmp_path
):
    monkeypatch.setattr(tasks_router, 'VIBE_SELLER_DIR', tmp_path)
    task_id = await _seed_running_task(override_async_session)
    _spend(task_id)

    r = await admin_client.post(
        f'/api/tasks/{task_id}/result', json={'result': 'Done.'}
    )
    assert r.status_code == 200, r.text
    assert task_id not in _ledgers, (
        'persisting the result must drop the re-drive ledger'
    )


async def test_delete_drops_the_redrive_ledger(admin_client):
    store = (
        await admin_client.post('/api/stores', json={'name': 'Ledger Store'})
    ).json()
    made = await admin_client.post(
        '/api/tasks',
        json={'title': 'ledger', 'store_id': store['id'], 'defer_start': True},
    )
    task_id = made.json()['id']
    _spend(task_id)

    r = await admin_client.delete(f'/api/tasks/{task_id}')
    assert r.status_code == 200, r.text
    assert task_id not in _ledgers, (
        'deleting a task must drop its re-drive ledger'
    )


async def test_a_follow_up_turn_starts_with_a_fresh_ledger(
    admin_client, install_fake_agent
):
    """A follow-up is a new user turn: it must not inherit the hold
    spend left on the task, or its first re-drive could already be out
    of attempts or out of clock."""
    store = (
        await admin_client.post('/api/stores', json={'name': 'Turn Store'})
    ).json()
    made = await admin_client.post(
        '/api/tasks',
        json={
            'title': 'first turn',
            'description': 'x',
            'store_id': store['id'],
        },
    )
    task_id = made.json()['id']
    await wait_for_task(admin_client, task_id)

    # Spend left on the task (a session of the earlier turn), then hold
    # the follow-up's agent open so the entry is observed mid-turn,
    # before the terminal reset could explain its absence.
    _spend(task_id)
    gate = asyncio.Event()
    install_fake_agent.default_scenario = FakeAgentScenario(gate=gate)
    r = await admin_client.post(
        f'/api/tasks/{task_id}/messages', json={'content': 'one more thing'}
    )
    assert r.status_code == 200, r.text
    await wait_for_task(admin_client, task_id, 'running')
    try:
        assert task_id not in _ledgers, (
            'a new turn must start the hold budget over'
        )
    finally:
        gate.set()
    await wait_for_task(admin_client, task_id)
