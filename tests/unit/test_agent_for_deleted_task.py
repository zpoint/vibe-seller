"""No agent runs for a task that no longer exists.

Observed: a task created and deleted within a second (a UI test cleaning
up after itself) was deleted while its launch still waited on the agent
slot and the workspace copy. The launch went on, the agent started with
no task row, and nothing could stop it -- the stop endpoint answers 404
for a deleted task -- so it ran on for minutes, roaming the workspace.
"""

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.ai import (
    claude_backend_manager as mgr_mod,
    claude_backend_persist as persist_mod,
)
from app.models.base import Base

pytestmark = pytest.mark.unit


@pytest.fixture
async def empty_db(monkeypatch):
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
    monkeypatch.setattr(mgr_mod, 'async_session', maker)
    monkeypatch.setattr(persist_mod, 'async_session', maker)
    yield maker
    await engine.dispose()


async def test_the_launch_does_not_start_an_agent_for_a_deleted_task(
    empty_db, monkeypatch, tmp_path
):
    started = []

    class _Session:
        def __init__(self, *a, **kw):
            started.append(a)

    async def _no_wait():
        return None

    async def _no_exclusions(*_a):
        return set()

    async def _workspace(task_id, **_kw):
        d = tmp_path / task_id
        d.mkdir()
        return d

    monkeypatch.setattr(mgr_mod, 'AgentSession', _Session)
    monkeypatch.setattr(mgr_mod.skills_sync, 'wait_deps_ready', _no_wait)
    monkeypatch.setattr(mgr_mod, '_ads_skill_exclusions', _no_exclusions)
    monkeypatch.setattr(
        mgr_mod.workspace_manager, 'prepare_task_workspace', _workspace
    )
    manager = mgr_mod.ClaudeCodeBackend()
    free_before = manager._semaphore._value

    ok = await manager.run('gone-task', 'p', no_store=True)

    assert ok is False
    assert started == []
    assert manager._semaphore._value == free_before
    assert not (tmp_path / 'gone-task').exists()


async def test_an_agent_that_starts_for_a_deleted_task_stops_itself(
    empty_db,
):
    """The launcher's check and the spawn are not atomic: an agent whose
    task vanished in between stops at its first event."""
    stopped = asyncio.Event()

    async def _stop():
        stopped.set()

    session = SimpleNamespace(task_id='gone-task', stop=_stop)
    await persist_mod._PersistMixin._persist_session_id(session, 'sess-1')
    await asyncio.wait_for(stopped.wait(), timeout=2)
