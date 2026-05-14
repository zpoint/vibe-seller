"""Unit tests for early session_id persistence in AgentSession.

Regression guard for CI e2e failure: when Claude Code's subprocess
hangs after emitting its final `result` event, `_save_result` (which
writes `task.session_id`) never runs. Persisting at the init event
avoids that — task.session_id becomes usable the moment Claude Code
tells us what it is.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.ai.claude_backend import AgentSession
from app.database import Base
from app.models.task import Task

pytestmark = pytest.mark.unit


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
                id='task-early-persist',
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


def _make_session(
    task_id: str = 'task-early-persist',
    resume_session_id: str | None = None,
) -> AgentSession:
    s = AgentSession(task_id=task_id, prompt='hi', mode='auto')
    s.resume_session_id = resume_session_id
    return s


class TestEarlyPersistOnInit:
    async def test_init_event_persists_session_id(self, _db):
        """First system/init event with a session_id must write
        task.session_id to the DB immediately — before the process
        exits and before _save_result runs.
        """
        with patch('app.ai.claude_backend_stream.async_session', _db):
            session = _make_session()
            assert session.session_id is None

            await session._handle_event({
                'type': 'system',
                'subtype': 'init',
                'session_id': 'new-sid-123',
            })

            # In-memory capture
            assert session.session_id == 'new-sid-123'
            # Authoritative DB write
            async with _db() as db:
                t = await db.get(Task, 'task-early-persist')
                assert t.session_id == 'new-sid-123'

    async def test_resumed_run_does_not_overwrite_root_id(self, _db):
        """When resume_session_id is set, the init's new id must NOT
        overwrite task.session_id — the root transcript id is what
        future follow-ups need to chain off.
        """
        with patch('app.ai.claude_backend_stream.async_session', _db):
            # Seed DB with root session id
            async with _db() as db:
                t = await db.get(Task, 'task-early-persist')
                t.session_id = 'root-sid'
                await db.commit()

            session = _make_session(resume_session_id='root-sid')
            await session._handle_event({
                'type': 'system',
                'subtype': 'init',
                'session_id': 'fresh-resume-sid',
            })

            async with _db() as db:
                t = await db.get(Task, 'task-early-persist')
                assert t.session_id == 'root-sid', (
                    'Resumed session must not overwrite the root id'
                )

    async def test_non_init_system_events_do_not_persist(self, _db):
        """Only the init subtype triggers persistence; later system
        events (e.g. api_retry) reuse the same session but should not
        trigger extra DB writes on their own (they'd be no-ops but we
        also don't want surprise reads).
        """
        persist = AsyncMock()
        with patch('app.ai.claude_backend_stream.async_session', _db):
            session = _make_session()
            with patch.object(session, '_persist_session_id', persist):
                await session._handle_event({
                    'type': 'system',
                    'subtype': 'api_retry',
                    'session_id': 'sid-2',
                    'error': 'transient',
                })
            persist.assert_not_called()
