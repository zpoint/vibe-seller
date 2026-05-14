"""Regression test for the skip-ExitPlanMode gap.

If an agent ends its plan-mode session without calling
ExitPlanMode (e.g. an LLM that decides "nothing to plan" and
returns a summary directly), ``_finalize_terminal_state``
previously treated that as a success when ``task.result`` was set.
For a regular plan-mode task that's correct — MiniMax-style
"skip plan and just do it" is a valid flow. For ``is_plan_only``
tasks (the schedule's plan-authoring Task) it's wrong: there is
nothing to execute, only a plan to produce, and silent success
leaves ``Schedule.plan`` empty and the schedule stuck at
``plan_status='planning'`` forever.

Fix: ``_finalize_terminal_state`` checks ``task.is_plan_only``
FIRST — plan-only tasks always fail if the plan wasn't written.
Normal plan-mode tasks keep the skip-plan success path.
"""

from datetime import UTC, datetime
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

import app.database as _db
from app.models.base import Base
from app.models.task import Task
import app.task_runner_auto as _auto

pytestmark = pytest.mark.unit


class _FakeManager:
    """Minimal agent_manager stub — finalizer only needs get_session."""

    def __init__(self):
        self._sessions: dict[str, object] = {}

    def get_session(self, task_id: str):
        return self._sessions.get(task_id)


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
    monkeypatch.setattr(_auto, 'async_session', maker)
    fake_mgr = _FakeManager()
    monkeypatch.setattr(_auto, 'agent_manager', fake_mgr)
    yield maker, fake_mgr


async def _seed_task(maker, *, is_plan_only: bool, result: str | None):
    """DESIGNING + plan_mode=True + no plan — the state the finalizer
    inspects for "agent left plan phase without producing a plan"."""
    task_id = str(uuid.uuid4())
    async with maker() as db:
        db.add(
            Task(
                id=task_id,
                title='t',
                status='designing',
                plan_mode=True,
                is_plan_only=is_plan_only,
                plan=None,
                result=result,
                created_by='u',
                updated_at=datetime.now(UTC).isoformat(),
            )
        )
        await db.commit()
    return task_id


class TestFinalizerPlanOnlySkip:
    async def test_plan_only_no_plan_with_result_fails(self, env):
        """is_plan_only + DESIGNING + no plan + has result → FAILED.

        Without the fix this would silently COMPLETE. The reaper
        would then see COMPLETED + empty Schedule.plan and (before
        the reaper companion fix) fall through all branches — the
        schedule would be stuck indefinitely."""
        maker, mgr = env
        task_id = await _seed_task(
            maker, is_plan_only=True, result='no-op, nothing to plan'
        )
        await _auto._finalize_terminal_state(task_id, my_session=None)
        async with maker() as db:
            t = await db.get(Task, task_id)
            assert t.status == 'failed', (
                f'Plan-only task must fail when no plan was saved; '
                f'got {t.status}. Without the fix this is silently '
                f"'completed', leaving Schedule.plan_status='planning'."
            )
            assert 'ExitPlanMode' in (t.error or '')
            assert t.error_category == 'plan_missing'

    async def test_plan_only_no_plan_no_result_fails(self, env):
        """Same rule when there's no result either — pure failure."""
        maker, _ = env
        task_id = await _seed_task(maker, is_plan_only=True, result=None)
        await _auto._finalize_terminal_state(task_id, my_session=None)
        async with maker() as db:
            t = await db.get(Task, task_id)
            assert t.status == 'failed'
            assert t.error_category == 'plan_missing'

    async def test_normal_plan_mode_skip_plan_still_completes(self, env):
        """Regression guard for the MiniMax-style skip-plan path —
        normal plan-mode tasks (is_plan_only=False) that produce a
        result without calling ExitPlanMode are still treated as a
        success. The is_plan_only branch must NOT change this."""
        maker, _ = env
        task_id = await _seed_task(
            maker, is_plan_only=False, result='did the thing'
        )
        await _auto._finalize_terminal_state(task_id, my_session=None)
        async with maker() as db:
            t = await db.get(Task, task_id)
            assert t.status == 'completed', (
                f'Normal plan-mode skip-plan success path must keep '
                f'working; got {t.status}.'
            )

    async def test_normal_plan_mode_no_plan_no_result_fails(self, env):
        """And a normal task with no plan AND no result still fails
        (existing behavior). Sanity check."""
        maker, _ = env
        task_id = await _seed_task(maker, is_plan_only=False, result=None)
        await _auto._finalize_terminal_state(task_id, my_session=None)
        async with maker() as db:
            t = await db.get(Task, task_id)
            assert t.status == 'failed'
            # Existing message (not the plan_only one).
            assert 'Design phase did not produce a plan' in (t.error or '')
