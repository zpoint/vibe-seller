"""Regression guard: ``stream_event`` lines must be consumed, not persisted.

Every agent spawn env carries ``CLAUDE_CODE_INCLUDE_PARTIAL_MESSAGES=1``
(``ensure_stream_liveness_env``), so the CLI forwards raw SSE parts as
``{type:'stream_event', event:{...}}`` lines — message_start, content
block starts/deltas/stops, pings. Two contracts:

- They are liveness, not content: the any-line heartbeat in
  ``_stream_output`` bumps ``Task.updated_at`` for every stdout line,
  which is the entire point of the flag (a healthy slow compose stops
  being stdout silence). The handler must NOT re-emit or persist them.
- Before the dedicated branch existed, these lines fell into the
  unknown-type ``else`` of ``_handle_event``, which persists each one
  as an ``agent_event`` TaskMessage row — hundreds of rows per turn
  and an equal number of SSE ``task_message`` frames.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.ai.claude_backend import AgentSession
from app.database import Base
from app.events.bus import event_bus
from app.models.task import Task
from app.models.task_message import TaskMessage

pytestmark = pytest.mark.unit


_TASK_ID = 'task-stream-event'


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
    async with maker() as db:
        db.add(
            Task(
                id=_TASK_ID,
                title='t',
                created_by='u',
                status='running',
                priority=1,
                plan_mode=False,
                created_at=datetime.now(UTC).isoformat(),
                updated_at=datetime.now(UTC).isoformat(),
            )
        )
        await db.commit()
    yield maker
    await engine.dispose()


def _stream_event(part: dict) -> dict:
    return {'type': 'stream_event', 'event': part, 'session_id': 's'}


class TestStreamEventConsumed:
    async def test_parts_not_persisted_or_emitted(self, _db, monkeypatch):
        """One of each part kind flows through ``_handle_event`` with
        zero TaskMessage rows and zero SSE task_message frames — the
        branch exists precisely to keep them out of the agent_event
        fallthrough."""
        monkeypatch.setattr(
            'app.ai.claude_backend_stream.async_session', _db
        )
        emit = AsyncMock()
        monkeypatch.setattr(event_bus, 'emit', emit)

        session = AgentSession(task_id=_TASK_ID, prompt='hi', mode='auto')
        for part in (
            {'type': 'message_start', 'message': {'id': 'm1'}},
            {'type': 'content_block_start', 'index': 0,
             'content_block': {'type': 'text', 'text': ''}},
            {'type': 'content_block_delta', 'index': 0,
             'delta': {'type': 'text_delta', 'text': 'FAKE'}},
            {'type': 'content_block_delta', 'index': 0,
             'delta': {'type': 'input_json_delta',
                       'partial_json': '{"a": 1}'}},
            {'type': 'ping'},
            {'type': 'content_block_stop', 'index': 0},
        ):
            await session._handle_event(_stream_event(part))

        async with _db() as db:
            rows = (await db.execute(
                select(TaskMessage).where(TaskMessage.task_id == _TASK_ID)
            )).scalars().all()
        assert rows == [], (
            'stream_event parts must not be persisted as messages; '
            f'got {len(rows)} rows'
        )
        emitted_roles = [
            c.args[1].get('role')
            for c in emit.call_args_list
            if c.args and c.args[0] == 'task_message'
        ]
        assert emitted_roles == [], (
            'stream_event parts must not be re-emitted over SSE '
            '(UI behaviour unchanged); got roles '
            f'{emitted_roles}'
        )

    async def test_unknown_event_still_falls_through(self, _db, monkeypatch):
        """The agent_event fallthrough keeps working for genuinely
        unknown types — the new branch must not swallow them."""
        monkeypatch.setattr(
            'app.ai.claude_backend_stream.async_session', _db
        )
        session = AgentSession(task_id=_TASK_ID, prompt='hi', mode='auto')
        await session._handle_event({'type': 'exotic_future_thing'})

        async with _db() as db:
            rows = (await db.execute(
                select(TaskMessage).where(TaskMessage.task_id == _TASK_ID)
            )).scalars().all()
        assert len(rows) == 1
        assert rows[0].role == 'agent_event'
