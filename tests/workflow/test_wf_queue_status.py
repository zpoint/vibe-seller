"""Workflow tests for task status transitions through the queue scheduler.

These tests start the real TaskQueueScheduler to verify that tasks
routed through the queue have correct status transitions — the gap
that allowed the "stuck at queued" bug.
"""

import pytest

from app.scheduler.task_queue import TaskQueueScheduler
from tests.workflow.conftest import wait_for_task

pytestmark = pytest.mark.workflow


async def _create_store(client, name='Queue Test Store'):
    r = await client.post('/api/stores', json={'name': name})
    assert r.status_code == 200
    return r.json()['id']


@pytest.fixture
async def with_queue(monkeypatch, override_async_session, mock_browser_wf):
    """Start a fresh queue scheduler for queue-aware tests.

    Patches the global singleton so schedule_or_run sees
    is_running=True and routes through submit().
    """
    scheduler = TaskQueueScheduler()
    monkeypatch.setattr('app.routers.tasks.task_queue_scheduler', scheduler)
    await scheduler.start()
    yield scheduler
    await scheduler.stop()


class TestQueuePlanMode:
    """Plan-mode store tasks through the queue — the original bug."""

    async def test_plan_mode_store_task_reaches_running(
        self,
        admin_client,
        install_fake_agent,
        with_queue,
    ):
        """Plan-mode store task: PENDING → QUEUED → DESIGNING →
        PLANNED → (execute) → RUNNING → COMPLETED.

        This is the exact scenario that was broken: execute-plan
        re-submitted through queue, status stuck at QUEUED.
        """
        store_id = await _create_store(admin_client)
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Queue plan test',
                'plan_mode': True,
                'store_id': store_id,
            },
        )
        assert r.status_code == 200
        task_id = r.json()['id']

        # Wait for plan to be produced
        data = await wait_for_task(admin_client, task_id, target='planned')
        assert data['status'] == 'planned'
        assert data['plan'] is not None

        # Execute the plan — goes through queue
        r = await admin_client.post(f'/api/tasks/{task_id}/execute-plan')
        assert r.status_code == 200

        # Should reach completed (not stuck at queued)
        data = await wait_for_task(admin_client, task_id)
        assert data['status'] == 'completed'


class TestQueueAutoMode:
    """Auto-mode store tasks through the queue."""

    async def test_auto_mode_store_task_completes(
        self,
        admin_client,
        install_fake_agent,
        with_queue,
    ):
        """Auto-mode: PENDING → QUEUED → RUNNING → COMPLETED."""
        store_id = await _create_store(admin_client)
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Queue auto test',
                'plan_mode': False,
                'store_id': store_id,
            },
        )
        assert r.status_code == 200
        task_id = r.json()['id']

        data = await wait_for_task(admin_client, task_id)
        assert data['status'] == 'completed'
