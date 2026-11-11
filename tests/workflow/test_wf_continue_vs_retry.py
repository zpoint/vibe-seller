"""Continue vs Retry contract.

The task-detail panel shows both on a failed task:
  - Continue posts /messages → RESUME the same task, preserving context
    (plan / plan_history stay; only stale run-error is cleared).
  - Retry posts /retry → DESTRUCTIVE fresh restart (plan / plan_history /
    error wiped, prior messages deleted, durable skill bindings
    cleared, back to PENDING).

These tests pin that split so a future change can't silently make
Continue destructive or Retry non-destructive.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.ai.stop_gates import GateDeny, record_skill_load, recorded_skills
import app.database as _db
from app.models.task import Task
from app.models.task_message import TaskMessage
from app.models.user import User
import app.routers.tasks as tasks_router
from tests.workflow.conftest import wait_for_task
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


class _RefusingAdGate:
    """Stand-in for an ad skill's exit gate: refuses any result once,
    the way ``ad_coverage`` refuses one without the TSVs it owes."""

    @staticmethod
    def check(result_text, task_id=None, rules=None):
        return GateDeny(gate='ad_gate', reason='ad coverage TSVs missing')


def _bind_gates_to_durable_loads(install_fake_agent, monkeypatch):
    """Make the submit path resolve gates from the durable bindings,
    mirroring the real ``ClaudeCodeBackend.loaded_skills_and_workspace``
    (the fake returns no skills of its own by design)."""
    monkeypatch.setattr(
        install_fake_agent,
        'loaded_skills_and_workspace',
        lambda task_id: (recorded_skills(task_id), None),
    )
    monkeypatch.setattr(
        tasks_router,
        'resolve_skill_gates',
        lambda loaded, _workspace: (
            [('ad_gate', _RefusingAdGate)] if 'amazon-ads' in loaded else []
        ),
    )


class TestSkillBindingsFollowTheRun:
    """A skill→task gate binding belongs to the run that loaded the
    skill. Retry is a fresh start, so the retried run must not be
    checked by the gates of a skill it never loaded; a follow-up
    continues the same work, so its gates stay."""

    async def test_retry_clears_skill_bindings(
        self, admin_client, install_fake_agent, monkeypatch
    ):
        task_id = await _seed_failed_task()
        # What run 1's PreToolUse hook wrote when it loaded the skill.
        record_skill_load(task_id, 'amazon-ads')
        _bind_gates_to_durable_loads(install_fake_agent, monkeypatch)
        install_fake_agent.default_scenario = FakeAgentScenario(
            gate=asyncio.Event()
        )

        r = await admin_client.post(f'/api/tasks/{task_id}/retry', json={})
        assert r.status_code == 200
        assert recorded_skills(task_id) == frozenset()

        # The retried run submits a non-ad result: nothing refuses it.
        await wait_for_task(admin_client, task_id, 'running')
        r = await admin_client.post(
            f'/api/tasks/{task_id}/result',
            json={'result': 'Plain answer, no ad work in it.'},
        )
        install_fake_agent.default_scenario.gate.set()
        assert r.status_code == 200, r.text

    async def test_followup_keeps_skill_bindings(
        self, admin_client, install_fake_agent, monkeypatch
    ):
        task_id = await _seed_failed_task()
        record_skill_load(task_id, 'amazon-ads')
        _bind_gates_to_durable_loads(install_fake_agent, monkeypatch)
        install_fake_agent.default_scenario = FakeAgentScenario(
            gate=asyncio.Event()
        )

        r = await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={'content': 'continue, summarize what we found'},
        )
        assert r.status_code == 200
        assert recorded_skills(task_id) == {'amazon-ads'}

        # The follow-up continues the ad work: the binding's gate gets
        # its one round — refused once, then shipped with caveats.
        await wait_for_task(admin_client, task_id, 'running')
        r = await admin_client.post(
            f'/api/tasks/{task_id}/result',
            json={'result': 'Summary of the ad audit.'},
        )
        assert r.status_code == 400, r.text
        r = await admin_client.post(
            f'/api/tasks/{task_id}/result',
            json={'result': 'Summary of the ad audit.'},
        )
        install_fake_agent.default_scenario.gate.set()
        assert r.status_code == 200, r.text
