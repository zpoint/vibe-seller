"""A task's gate state goes with the task.

The spent review rounds are in memory and the frozen ad approval is a
file under DATA_DIR, both keyed by task id. Neither may outlive the run:
the rounds are dropped when the task ends or is deleted, the approval
when the task is deleted or retried (a retry is a fresh run, and an
execution must never be checked against a previous run's approval).
"""

import uuid

import pytest
from sqlalchemy import select

from app.ai import stop_gates
from app.ai.stop_gates import ad_changes
import app.database as _db
from app.models.task import Task
from app.models.user import User
from app.task_states import TaskStatus

pytestmark = pytest.mark.workflow


async def _seed(status: str) -> str:
    task_id = str(uuid.uuid4())
    async with _db.async_session() as db:
        u = (await db.execute(select(User).limit(1))).scalars().first()
        db.add(
            Task(
                id=task_id,
                title='audit',
                status=status,
                plan_mode=False,
                created_by=u.id,
            )
        )
        await db.commit()
    return task_id


def _freeze(task_id: str):
    path = ad_changes.approved_path(task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('campaign_id\tfield\n')
    return path


def _spend_round(task_id: str) -> None:
    assert stop_gates.take_review_round(task_id, 'ad_coverage')
    assert stop_gates.review_rounds_spent(task_id, 'ad_coverage') == 1


async def test_terminal_state_drops_review_rounds(admin_client):
    task_id = await _seed(TaskStatus.RUNNING)
    _spend_round(task_id)
    async with _db.async_session() as db:
        task = await db.get(Task, task_id)
        task.status = TaskStatus.COMPLETED
        await db.commit()
    assert stop_gates.review_rounds_spent(task_id, 'ad_coverage') == 0


async def test_delete_drops_review_rounds_and_approval(admin_client):
    task_id = await _seed(TaskStatus.WAITING)
    _spend_round(task_id)
    approved = _freeze(task_id)

    r = await admin_client.delete(f'/api/tasks/{task_id}')
    assert r.status_code == 200, r.text
    assert stop_gates.review_rounds_spent(task_id, 'ad_coverage') == 0
    assert not approved.exists()


async def test_retry_drops_the_approval(admin_client):
    task_id = await _seed(TaskStatus.FAILED)
    approved = _freeze(task_id)

    r = await admin_client.post(f'/api/tasks/{task_id}/retry', json={})
    assert r.status_code == 200, r.text
    assert not approved.exists()
