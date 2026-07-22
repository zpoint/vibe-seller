"""Continue vs Retry contract.

The task-detail panel shows both on a failed task:
  - Continue posts /messages → RESUME the same task, preserving context
    (plan / plan_history stay; only stale run-error is cleared).
  - Retry posts /retry → DESTRUCTIVE fresh restart (plan / plan_history /
    error wiped, prior messages deleted, back to PENDING).

These tests pin that split so a future change can't silently make
Continue destructive or Retry non-destructive.
"""

import uuid

import pytest
from sqlalchemy import select

import app.database as _db
from app.models.task import Task
from app.models.task_message import TaskMessage
from app.models.user import User
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow


async def _seed_failed_task():
    """A FAILED, non-plan task carrying plan + plan_history + error +
    a prior conversation message."""
    task_id = str(uuid.uuid4())
    async with _db.async_session() as db:
        u = (await db.execute(select(User).limit(1))).scalars().first()
        db.add(
            Task(
                id=task_id,
                title='audit',
                description='drill all campaigns',
                status='failed',
                plan_mode=False,
                plan='## prior plan\ndrill everything',
                plan_history='["## prior plan"]',
                error='browser infra failure',
                error_category='browser',
                created_by=u.id,
            )
        )
        db.add(
            TaskMessage(
                task_id=task_id,
                role='assistant',
                content='partial progress: 15/34 drilled',
                seq=0,
            )
        )
        await db.commit()
    return task_id


async def _get_task(task_id: str) -> Task:
    async with _db.async_session() as db:
        return await db.get(Task, task_id)


async def _msg_count(task_id: str) -> int:
    async with _db.async_session() as db:
        rows = (
            (
                await db.execute(
                    select(TaskMessage).where(TaskMessage.task_id == task_id)
                )
            )
            .scalars()
            .all()
        )
        return len(rows)


class TestContinueVsRetry:
    async def test_continue_resumes_and_preserves_context(
        self, admin_client, install_fake_agent
    ):
        """Continue: /messages on a FAILED task resumes (mode=auto), keeps
        the plan + plan_history, clears only the stale error."""
        install_fake_agent.default_scenario = FakeAgentScenario()
        task_id = await _seed_failed_task()

        r = await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={'content': 'continue the previous task, reuse progress'},
        )
        assert r.status_code == 200

        # Resumed via the follow-up path, not a fresh re-plan.
        run_calls = install_fake_agent.get_calls(task_id=task_id, action='run')
        assert run_calls, 'agent was not resumed'
        assert run_calls[-1].mode == 'auto'

        task = await _get_task(task_id)
        # Context preserved — the whole point of Continue.
        assert task.plan == '## prior plan\ndrill everything'
        assert task.plan_history == '["## prior plan"]'
        # Stale run-error cleared; task is no longer failed.
        assert task.error is None
        assert task.status in ('running', 'completed')
        # Prior conversation kept (plus the new user message).
        assert await _msg_count(task_id) >= 2

    async def test_retry_clears_context(self, admin_client):
        """Retry: /retry wipes plan/plan_history/error, deletes prior
        messages, and resets to PENDING (fresh restart)."""
        task_id = await _seed_failed_task()

        r = await admin_client.post(f'/api/tasks/{task_id}/retry', json={})
        assert r.status_code == 200

        task = await _get_task(task_id)
        assert task.plan is None
        assert task.plan_history is None
        assert task.error is None
        assert task.error_category is None
        assert task.status == 'pending'
        # Prior conversation wiped by the destructive retry.
        assert await _msg_count(task_id) == 0

    async def test_retry_clears_workspace_files(
        self, admin_client, mock_workspace
    ):
        """Retry wipes the on-disk task workspace, so a prior run's review
        dumps can't survive to be reused/gamed. This is the disk-side
        counterpart to test_retry_clears_context (which pins the DB side).
        """
        task_id = await _seed_failed_task()
        # A leftover review dump from a prior run.
        stale = (
            mock_workspace.root
            / 'tasks'
            / task_id
            / 'reviews'
            / 'amazon'
            / 'ae'
            / 'B0STALE.json'
        )
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_text('{"schema":"reviews/v1","rating":4.1}')

        r = await admin_client.post(f'/api/tasks/{task_id}/retry', json={})
        assert r.status_code == 200

        # The destructive retry cleared the workspace → stale file gone.
        assert not stale.exists()

    async def test_continue_preserves_workspace_files(
        self, admin_client, install_fake_agent, mock_workspace
    ):
        """Continue (POST /messages) must NOT wipe the workspace — it is
        the resume path, so banked run data (ad-audit reports/TSVs, an
        in-progress review dir) has to persist."""
        install_fake_agent.default_scenario = FakeAgentScenario()
        task_id = await _seed_failed_task()
        banked = (
            mock_workspace.root
            / 'tasks'
            / task_id
            / 'reviews'
            / 'amazon'
            / 'ae'
            / 'B0BANKED.json'
        )
        banked.parent.mkdir(parents=True, exist_ok=True)
        banked.write_text('{"schema":"reviews/v1","rating":4.1}')

        r = await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={'content': 'continue, reuse progress'},
        )
        assert r.status_code == 200

        # Continue preserved the banked file.
        assert banked.exists()
