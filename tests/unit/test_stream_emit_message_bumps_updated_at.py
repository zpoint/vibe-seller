"""Regression guard: `_emit_message` must bump `Task.updated_at`.

The stall-reaper keys off `Task.updated_at` to decide whether a
RUNNING task's upstream SSE stream has gone silent. Without a bump
from the streaming message path, only six lifecycle transitions
ever move `updated_at` forward — so a busy agent that emits
hundreds of tool_use/thinking/assistant events without hitting any
of those transitions would appear "stale" at exactly 5 min past the
PLANNED→RUNNING flip and get force-failed mid-execution.

This happened in prod (task d5cab700-629a-417c-9352-3e9b0c521aac,
2026-04-24) and slipped past existing unit tests because:

- `test_stall_reaper.py` seeds raw DB rows with artificially stale
  timestamps — it asserts the reaper's logic in isolation but
  never exercises the real `_emit_message` path that was supposed
  to keep `updated_at` fresh.
- `FakeAgent._create_message` in `tests/workflow/fake_agent.py`
  mirrored the bug (also never bumped `updated_at`), so workflow
  tests couldn't surface it either.
- Workflow tests finish in <10 s so they never cross the 5-min
  reaper threshold even accidentally.

The test below closes the gap at the component boundary: it runs
the real `_emit_message` and then invokes the real
`reap_stalled_running_tasks` to confirm the two work together.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.ai.claude_backend import AgentSession
import app.ai.claude_backend_manager as _cb
from app.database import Base
from app.models.task import Task
from app.models.task_message import TaskMessage
import app.scheduler.stall_reaper as _reaper
from app.task_states import TaskStatus

pytestmark = pytest.mark.unit


_TASK_ID = 'task-emit-updated'


@pytest.fixture
async def _db():
    engine = create_async_engine(
        'sqlite+aiosqlite://',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    # Seed RUNNING task whose `updated_at` is already 10 min stale —
    # exactly the prod situation: PLANNED→RUNNING flip happened long
    # ago and no lifecycle transition has bumped it since.
    stale = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    async with maker() as db:
        db.add(
            Task(
                id=_TASK_ID,
                title='t',
                created_by='u',
                status=TaskStatus.RUNNING,
                priority=1,
                plan_mode=False,
                created_at=stale,
                updated_at=stale,
            )
        )
        await db.commit()
    yield maker
    await engine.dispose()


def _make_session() -> AgentSession:
    return AgentSession(task_id=_TASK_ID, prompt='hi', mode='auto')


class _NoopManager:
    """Reaper calls `agent_manager.stop()` and `.get_pending_questions`.

    We don't want either to touch real backend state here.
    """

    async def stop(self, task_id: str) -> bool:
        return True

    def get_pending_questions(self, task_id: str) -> dict | None:
        return None


class TestEmitMessageBumpsUpdatedAt:
    async def test_emit_message_writes_task_updated_at(self, _db):
        """A single `_emit_message` call must move `updated_at` forward.

        Direct assertion: the only thing the fix adds is this bump,
        so we verify it in isolation before exercising it against the
        reaper.
        """
        with patch('app.ai.claude_backend_stream.async_session', _db):
            session = _make_session()
            async with _db() as db:
                before = (await db.get(Task, _TASK_ID)).updated_at

            await session._emit_message('thinking', 'hello')

            async with _db() as db:
                after = (await db.get(Task, _TASK_ID)).updated_at
                msgs = (await db.execute(TaskMessage.__table__.select())).all()
            assert after > before, (
                '_emit_message must bump Task.updated_at; '
                f'before={before} after={after}'
            )
            assert len(msgs) == 1, 'one task_message should have been written'

    async def test_active_agent_not_reaped_by_stall_reaper(
        self, _db, monkeypatch
    ):
        """End-to-end: an active agent emitting messages should NOT be
        force-failed by the stall-reaper even when the task row's
        original `updated_at` is already past the 5-min threshold.

        This is the cross-component test that would have caught the
        prod bug. Prior to the fix, `_emit_message` left the stale
        `updated_at` untouched and the reaper killed the task.
        """
        # Route both the stream module and the reaper module at the
        # same in-memory DB, and swap out agent_manager for a no-op
        # (the reaper also lives in its own module namespace).
        monkeypatch.setattr('app.ai.claude_backend_stream.async_session', _db)
        monkeypatch.setattr(_reaper, 'async_session', _db)
        fake = _NoopManager()
        monkeypatch.setattr(_reaper, 'agent_manager', fake)
        monkeypatch.setattr(_cb, 'agent_manager', fake)

        session = _make_session()
        # Simulate a busy agent emitting a handful of stream events
        # — the same shape real tool_use / thinking / assistant
        # messages take through `_emit_message`.
        for role in ('thinking', 'assistant', 'tool_use'):
            await session._emit_message(role, f'{role}-payload')

        # Run the reaper. With the fix, messages bumped `updated_at`
        # so the task is outside the stale window and must be left
        # alone. Without the fix, it would flip to FAILED.
        await _reaper.reap_stalled_running_tasks()

        async with _db() as db:
            t = await db.get(Task, _TASK_ID)
        assert t.status == TaskStatus.RUNNING, (
            'Active agent (emitting messages) must not be reaped; '
            f'got status={t.status}, error={t.error!r}'
        )
        assert t.error is None
