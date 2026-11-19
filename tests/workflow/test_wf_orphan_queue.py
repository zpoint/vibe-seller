"""A task waiting to start must always have something that will start it.

The bug: a row could say QUEUED while no queue lane, launcher coroutine
or slot wait held the task. Freed agent slots went to newer tasks, and
only a server restart (``_recover_from_db``) ever started it. Observed
as a daily scheduled task sitting ``queued`` for hours.

Two layers are pinned here:

- the proven entry path — waking a WAITING task with no store wrote
  QUEUED and never submitted it — now reaches the queue;
- the orphan reconciler re-queues ANY such row within one pass, and
  leaves alone every task that is merely waiting its turn.
"""

from datetime import UTC, datetime, timedelta
import json

import pytest

from app.database import get_db
from app.main import app as _app
from app.models.task import Task
from app.scheduler.task_queue import TaskQueueScheduler
from app.task_states import TaskStatus
from tests.workflow.conftest import wait_for_task

pytestmark = pytest.mark.workflow

_LONG_AGO = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()


@pytest.fixture
async def queue(
    monkeypatch, override_async_session, mock_browser_wf, install_fake_agent
):
    """A running queue, wired into every site that submits to it."""
    scheduler = TaskQueueScheduler()
    for site in (
        'app.routers.tasks',
        'app.routers.tasks_conversation',
        'app.scheduler.waiting',
    ):
        monkeypatch.setattr(f'{site}.task_queue_scheduler', scheduler)
    await scheduler.start()
    yield scheduler
    await scheduler.stop()


@pytest.fixture
def reconcile(monkeypatch, queue):
    """One pass of the orphan reconciler against the test queue."""

    async def _pass() -> int:
        # Imported here, not at the top: the regression must FAIL on
        # pre-fix code rather than stop the module from collecting.
        from app.scheduler import queue_reconciler  # noqa: PLC0415

        monkeypatch.setattr(queue_reconciler, 'task_queue_scheduler', queue)
        return await queue_reconciler.reconcile_orphaned_tasks()

    return _pass


async def _set_row(task_id: str, **fields) -> None:
    db_gen = _app.dependency_overrides[get_db]()
    db = await db_gen.__anext__()
    try:
        task = await db.get(Task, task_id)
        for k, v in fields.items():
            setattr(task, k, v)
        await db.commit()
    finally:
        await db_gen.aclose()


# Left the queue: the launcher took the task (a no-store task plans
# first and parks at PLANNED for review; a store task runs through).
_STARTED = ('designing', 'planned', 'running', 'completed')


async def _new_task(client, **body) -> str:
    """A task row nothing has launched (``defer_start``)."""
    r = await client.post(
        '/api/tasks',
        json={
            'title': 'orphan probe',
            'plan_mode': False,
            'defer_start': True,
            **body,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()['id']


def _runs(fake, task_id: str) -> int:
    return len(fake.get_calls(task_id=task_id, action='run'))


async def _orphan(client, fake, **body) -> str:
    """A QUEUED row with nothing holding it — the state the wake path
    (and any future path like it) leaves."""
    task_id = await _new_task(client, **body)
    await _set_row(task_id, status=TaskStatus.QUEUED, updated_at=_LONG_AGO)
    assert _runs(fake, task_id) == 0
    return task_id


class TestWakeStorelessTask:
    async def test_woken_storeless_task_runs(
        self, admin_client, install_fake_agent, queue
    ):
        """POST /wake on a no-store WAITING task must start it — it
        used to stay QUEUED until the server restarted."""
        task_id = await _new_task(admin_client)
        await _set_row(
            task_id,
            status=TaskStatus.WAITING,
            wait_condition=json.dumps({
                'reason': 'waiting for the operator',
                'check_strategy': 'manual',
            }),
        )

        r = await admin_client.post(f'/api/tasks/{task_id}/wake', json={})
        assert r.status_code == 200
        assert r.json()['status'] == 'queued'

        await wait_for_task(admin_client, task_id, _STARTED)
        assert _runs(install_fake_agent, task_id) == 1


class TestOrphanReconciler:
    async def test_storeless_orphan_is_started(
        self, admin_client, install_fake_agent, reconcile
    ):
        task_id = await _orphan(admin_client, install_fake_agent)

        assert await reconcile() == 1
        await wait_for_task(admin_client, task_id, _STARTED)
        assert _runs(install_fake_agent, task_id) == 1

    async def test_store_orphan_is_started_in_its_lane(
        self, admin_client, install_fake_agent, reconcile
    ):
        r = await admin_client.post('/api/stores', json={'name': 'store-a'})
        store_id = r.json()['id']
        task_id = await _orphan(
            admin_client, install_fake_agent, store_id=store_id
        )

        assert await reconcile() == 1
        await wait_for_task(admin_client, task_id, _STARTED)
        assert _runs(install_fake_agent, task_id) == 1

    async def test_held_tasks_are_left_alone(
        self, admin_client, install_fake_agent, queue, reconcile
    ):
        """Nothing that is waiting its turn is launched twice, and no
        row that is parked on purpose is launched at all."""
        from app import task_owners  # noqa: PLC0415

        # In a lane, behind a blocked head.
        in_lane = await _orphan(admin_client, install_fake_agent)
        queue._queues.setdefault('blocked-lane', []).append(in_lane)
        # Held by a launcher / slot wait.
        held = await _orphan(admin_client, install_fake_agent)
        # Just written: the launch path has not handed it over yet.
        fresh = await _orphan(admin_client, install_fake_agent)
        await _set_row(fresh, updated_at=datetime.now(UTC).isoformat())
        # Unscheduled PENDING: parked for /start (defer_start).
        deferred = await _new_task(admin_client)
        await _set_row(deferred, updated_at=_LONG_AGO)

        with task_owners.holding(held):
            assert await reconcile() == 0

        for task_id in (in_lane, held, fresh, deferred):
            assert _runs(install_fake_agent, task_id) == 0
        assert queue._queues['blocked-lane'] == [in_lane]
