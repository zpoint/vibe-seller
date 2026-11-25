"""A plan is reviewed once per task.

After the user approves a task's plan, the agent's later re-plans (a
follow-up asking it to revise and go again) run without another
Confirm, whether the follow-up reaches the live session or starts a new
one. A full retry starts the task over, so its plan is reviewed again.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.ai.claude_backend import AgentSession
import app.ai.claude_backend_hooks as hooks
from app.models.base import Base
from app.models.task import Task

pytestmark = pytest.mark.unit


@pytest_asyncio.fixture
async def planning_task(monkeypatch):
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
    monkeypatch.setattr(hooks, 'async_session', maker)
    task_id = str(uuid.uuid4())
    async with maker() as db:
        db.add(
            Task(
                id=task_id,
                title='Review me',
                status='planned',
                plan_mode=True,
                created_by='u',
            )
        )
        await db.commit()
    yield task_id
    await engine.dispose()


async def test_live_session_replan_after_approval_does_not_wait(planning_task):
    session = AgentSession(
        task_id=planning_task,
        prompt='test',
        mode='plan_then_execute',
        auto_approve_plan=False,
    )
    # A live agent process: input-capable, not exited.
    session._proc = SimpleNamespace(returncode=None)
    session._save_design_plan = AsyncMock()
    sent: list[str] = []

    async def _record(request_id: str, behavior: str, **kwargs):
        sent.append(f'{request_id}:{behavior}')

    session._send_control_response = _record

    first = asyncio.create_task(
        session._handle_exit_plan_mode_can_use_tool('r1', {'plan': 'v1'})
    )
    for _ in range(5):
        await asyncio.sleep(0)
    assert not first.done()  # the first plan waits for the user
    await session.approve_plan()
    await asyncio.wait_for(first, 2)
    assert sent == ['r1:allow']

    # A follow-up in the same session re-plans: it runs at once.
    await asyncio.wait_for(
        session._handle_exit_plan_mode_can_use_tool('r2', {'plan': 'v2'}), 2
    )
    assert sent == ['r1:allow', 'r2:allow']
    session._save_design_plan.assert_awaited_with(
        'v2', waits_for_approval=False
    )
