"""Unit tests for the fanout-plan validator in
``app/ai/claude_backend_utils.py.validate_fanout_plan_text``.

The validator runs when the agent calls ``ExitPlanMode`` on a
plan-only Task whose owning Schedule is in ``phase_mode='fanout'``.
It prevents the agent from embedding an orchestrator step (e.g.
``vibe_seller_create_task``) inside the plan — the scheduler's
fanout already creates per-store children each fire, so an
orchestrator step would cause recursive spawning.

Cases covered:
- Clean plan on a fanout schedule → None (accepted).
- Plan containing ``vibe_seller_create_task`` → rejected with a
  human-readable reason.
- Plan containing ``parent_task_id`` → rejected.
- Case-insensitive match.
- Plan on a SINGLE-mode schedule → None regardless of content
  (single-mode can legitimately orchestrate).
- Plan on a non-is_plan_only task → None (orchestration is fine
  for ad-hoc plan-mode tasks).
- Missing schedule row → None (fail open; don't block).
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

import app.ai.claude_backend_utils as _utils
import app.database as _db
from app.models.base import Base
from app.models.schedule import Schedule
from app.models.task import Task

pytestmark = pytest.mark.unit


@pytest_asyncio.fixture
async def env(monkeypatch):
    engine = create_async_engine(
        'sqlite+aiosqlite://',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr(_db, 'async_session', maker)
    monkeypatch.setattr(_utils, 'async_session', maker)
    yield maker


async def _seed(maker, *, is_plan_only: bool, phase_mode: str):
    sched_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    async with maker() as db:
        db.add(
            Schedule(
                id=sched_id,
                title='sched',
                schedule_type='days',
                schedule_time='09:05',
                plan_mode=True,
                phase_mode=phase_mode,
                created_by='u',
                timezone='UTC',
            )
        )
        db.add(
            Task(
                id=task_id,
                title='Plan: sched',
                status='designing',
                plan_mode=True,
                is_plan_only=is_plan_only,
                schedule_id=sched_id,
                created_by='u',
            )
        )
        await db.commit()
    return task_id


class TestFanoutPlanValidator:
    async def test_clean_plan_passes(self, env):
        task_id = await _seed(env, is_plan_only=True, phase_mode='fanout')
        reason = await _utils.validate_fanout_plan_text(
            task_id,
            '## Plan\n\n1. Read store L3 catalog\n2. Do per-store work.\n',
        )
        assert reason is None

    async def test_vibe_seller_create_task_rejected(self, env):
        task_id = await _seed(env, is_plan_only=True, phase_mode='fanout')
        reason = await _utils.validate_fanout_plan_text(
            task_id,
            '## Orchestrator\nCall `vibe_seller_create_task` per store.\n',
        )
        assert reason is not None
        assert 'sub-task MCP' in reason
        # Reason is meant to be shown to the agent — mentions the
        # offending needle so the agent can fix it.
        assert 'vibe_seller_create_task' in reason

    async def test_parent_task_id_rejected(self, env):
        task_id = await _seed(env, is_plan_only=True, phase_mode='fanout')
        reason = await _utils.validate_fanout_plan_text(
            task_id, 'For each sub-task, set parent_task_id = orchestrator id.'
        )
        assert reason is not None
        assert 'parent/sub-task' in reason

    async def test_case_insensitive(self, env):
        task_id = await _seed(env, is_plan_only=True, phase_mode='fanout')
        reason = await _utils.validate_fanout_plan_text(
            task_id, 'Call Vibe_Seller_Create_Task per store.'
        )
        assert reason is not None

    async def test_single_mode_schedule_not_validated(self, env):
        # Single-mode plans run exactly once per fire — orchestration
        # may be legitimate (e.g. a shared-mailbox sweep task that
        # wants to dispatch per-tenant work).
        task_id = await _seed(env, is_plan_only=True, phase_mode='single')
        reason = await _utils.validate_fanout_plan_text(
            task_id, 'Call vibe_seller_create_task per tenant.'
        )
        assert reason is None

    async def test_non_plan_only_task_not_validated(self, env):
        # A scheduled fire or interactive plan-mode task is exempt —
        # the validator only guards the plan-authoring phase.
        task_id = await _seed(env, is_plan_only=False, phase_mode='fanout')
        reason = await _utils.validate_fanout_plan_text(
            task_id, 'Call vibe_seller_create_task per store.'
        )
        assert reason is None

    async def test_missing_schedule_fails_open(self, env):
        # Orphan task (no owning schedule) → validator cannot verify
        # constraints, returns None instead of blocking. The
        # plan-at-creation router guarantees this doesn't happen in
        # practice.
        sched_missing_id = str(uuid.uuid4())
        task_id = str(uuid.uuid4())
        async with env() as db:
            db.add(
                Task(
                    id=task_id,
                    title='orphan',
                    status='designing',
                    plan_mode=True,
                    is_plan_only=True,
                    schedule_id=sched_missing_id,
                    created_by='u',
                )
            )
            await db.commit()
        reason = await _utils.validate_fanout_plan_text(
            task_id, 'vibe_seller_create_task everywhere'
        )
        assert reason is None
