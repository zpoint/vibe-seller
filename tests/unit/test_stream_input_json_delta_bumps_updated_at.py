"""Regression guard: `input_json_delta` events must bump `Task.updated_at`.

When the LLM is generating a tool's input (e.g. a multi-KB Write
call's ``content`` argument), the SSE stream emits
``content_block_delta`` events whose ``delta.type`` is
``input_json_delta`` carrying chunks of the JSON.  These are the only
events flowing while the model composes a long tool input.

Before this fix, ``_handle_event`` only routed ``text_delta`` and
``thinking_delta`` through ``_emit_ephemeral`` (which bumps
``Task.updated_at``).  ``input_json_delta`` was logged for diagnostics
but had no heartbeat side-effect — so a busy agent generating ~32 KB
of markdown via ``Write`` for 5+ minutes appeared "stale" to the
stall reaper and got force-failed mid-generation.

This happened on task 73032910 (2026-05-02) and slipped past the
existing ``test_stream_emit_message_bumps_updated_at`` because that
test only exercised the assistant/thinking/tool_use *boundary*
events — not mid-message deltas.

The test below feeds a synthetic ``content_block_delta`` /
``input_json_delta`` event into the real ``_handle_event`` and then
runs the real ``reap_stalled_running_tasks`` to confirm the agent
isn't reaped.
"""

from datetime import UTC, datetime, timedelta

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
import app.scheduler.stall_reaper as _reaper
from app.task_states import TaskStatus

pytestmark = pytest.mark.unit


_TASK_ID = 'task-input-json-delta'


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
    # Stale RUNNING task — updated_at is already 10 min behind, mimics
    # the prod situation where the agent has been generating a long
    # tool input for several minutes with no other events firing.
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
    async def stop(self, task_id: str) -> bool:
        return True

    def get_pending_questions(self, task_id: str) -> dict | None:
        return None


def _input_json_delta_event(partial_json: str) -> dict:
    return {
        'type': 'content_block_delta',
        'index': 0,
        'delta': {
            'type': 'input_json_delta',
            'partial_json': partial_json,
        },
    }


class TestInputJsonDeltaBumpsUpdatedAt:
    async def test_input_json_delta_writes_updated_at(self, _db, monkeypatch):
        """A single ``input_json_delta`` event bumps ``Task.updated_at``.

        Direct assertion at the component boundary: feeding the event
        through the real ``_handle_event`` path must move
        ``updated_at`` forward.  Otherwise the stall reaper has no
        signal that the agent is actively composing tool input.
        """
        monkeypatch.setattr('app.ai.claude_backend_stream.async_session', _db)

        async with _db() as db:
            before = (await db.get(Task, _TASK_ID)).updated_at

        session = _make_session()
        await session._handle_event(
            _input_json_delta_event('{"content": "# Audit ')
        )

        async with _db() as db:
            after = (await db.get(Task, _TASK_ID)).updated_at
        assert after > before, (
            'input_json_delta must bump Task.updated_at; '
            f'before={before} after={after}'
        )

    async def test_active_write_generation_not_reaped(self, _db, monkeypatch):
        """End-to-end: an agent streaming ``input_json_delta`` chunks
        for a long Write call must NOT be force-failed by the stall
        reaper, even with the row's original ``updated_at`` already
        past the 5-min threshold.

        Before the fix, ``input_json_delta`` events left
        ``updated_at`` untouched.  The reaper would kill the agent
        mid-Write.  This is the cross-component test that pins the
        contract.
        """
        monkeypatch.setattr('app.ai.claude_backend_stream.async_session', _db)
        monkeypatch.setattr(_reaper, 'async_session', _db)
        fake = _NoopManager()
        monkeypatch.setattr(_reaper, 'agent_manager', fake)
        monkeypatch.setattr(_cb, 'agent_manager', fake)

        session = _make_session()
        # Simulate the LLM streaming a Write call's `content` argument
        # in chunks — five chunks is enough to clear the 60s throttle
        # and exercise the full path.
        for chunk in (
            '{"content": "# Audit\\n',
            '\\n## Country 1: AE\\n',
            '\\n## Country 2: SA\\n',
            '\\n## Action checklist\\n',
            '"}',
        ):
            await session._handle_event(_input_json_delta_event(chunk))

        await _reaper.reap_stalled_running_tasks()

        async with _db() as db:
            t = await db.get(Task, _TASK_ID)
        assert t.status == TaskStatus.RUNNING, (
            'Active agent streaming input_json_delta must not be '
            f'reaped; got status={t.status}, error={t.error!r}'
        )
        assert t.error is None
