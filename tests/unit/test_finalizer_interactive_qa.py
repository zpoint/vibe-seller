"""Regression test for the interactive-Q&A prose-follow-up gap.

Observed in production: an auto-mode agent called
AskUserQuestion, the operator answered, and the agent replied with a
PROSE follow-up question ("please give me the image path") — then
ended its turn without taking any further action. The streaming-prose
fallback persisted that question into ``task.result``, so
``_finalize_terminal_state``'s waiting-park branches (all gated on
``not task.result``) never fired, and the task silently transitioned
to COMPLETED while the operator was mid-answer.

The invariant this pins: **a task whose agent engaged in interactive
Q&A and then produced prose ONLY after the last answer — no follow-up
tool action, not even the explicit ``vibe_seller_set_task_result``
(itself a tool_use) — is WAITING for input, never COMPLETED.**

The discriminator is structural, not a content heuristic:
``session._asked_user_question`` (a question was asked) AND NOT
``session._tool_use_since_answer`` (no real tool ran after the last
answer). A single-turn task that never asked anything is unaffected —
which is why the no-regression cases below matter as much as the fix
case. FakeAgent sessions lack these attrs entirely (getattr default
False), so every workflow finalizer test keeps completing as before.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
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


async def _seed_running_task(maker):
    """Auto-mode task at the point the agent's process just ended:
    RUNNING, plan_mode=False, a (prose) result persisted, no todos."""
    task_id = str(uuid.uuid4())
    async with maker() as db:
        db.add(
            Task(
                id=task_id,
                title='白底主图',
                status='running',
                plan_mode=False,
                is_plan_only=False,
                plan=None,
                result='请告诉我图片文件的路径（绝对路径），我来接着处理。',
                todos=None,
                created_by='u',
                updated_at=datetime.now(UTC).isoformat(),
            )
        )
        await db.commit()
    return task_id


def _session(mgr, task_id, **flags):
    """A stand-in ClaudeCodeBackend session carrying the finalize
    signals. ``_is_error_result`` False so we reach the completion
    zone; the Q&A flags come from ``flags``."""
    sess = SimpleNamespace(
        _is_error_result=False,
        _agent_success=True,
        **flags,
    )
    mgr._sessions[task_id] = sess
    return sess


class TestFinalizerInteractiveQA:
    async def test_asked_then_prose_only_parks_waiting(self, env):
        """THE FIX: asked a question, answered, replied with prose and
        took no further action → WAITING, not COMPLETED."""
        maker, mgr = env
        task_id = await _seed_running_task(maker)
        sess = _session(
            mgr,
            task_id,
            _asked_user_question=True,
            _tool_use_since_answer=False,
        )
        await _auto._finalize_terminal_state(task_id, my_session=sess)
        async with maker() as db:
            t = await db.get(Task, task_id)
            assert t.status == 'waiting', (
                f'Agent asked a prose follow-up after an answered '
                f'AskUserQuestion — must park WAITING for the operator, '
                f'not silently complete; got {t.status}.'
            )
            assert t.wait_condition, 'WAITING must record a wait_condition'

    async def test_asked_then_tool_action_completes(self, env):
        """Converse: after the last answer the agent DID take a tool
        action (a real step, or the explicit set_task_result tool) →
        it finished → COMPLETED."""
        maker, mgr = env
        task_id = await _seed_running_task(maker)
        _session(
            mgr,
            task_id,
            _asked_user_question=True,
            _tool_use_since_answer=True,
        )
        sess = mgr.get_session(task_id)
        await _auto._finalize_terminal_state(task_id, my_session=sess)
        async with maker() as db:
            t = await db.get(Task, task_id)
            assert t.status == 'completed', (
                f'A tool action after the answer means the agent worked '
                f'toward completion; got {t.status}.'
            )

    async def test_never_asked_completes(self, env):
        """No-regression: a single-turn task that never called
        AskUserQuestion completes normally even though no post-answer
        tool ran (there was no answer). This is the guard that keeps
        ordinary prose completions — e.g. a short text answer — from
        being mis-parked as WAITING."""
        maker, mgr = env
        task_id = await _seed_running_task(maker)
        _session(
            mgr,
            task_id,
            _asked_user_question=False,
            _tool_use_since_answer=False,
        )
        sess = mgr.get_session(task_id)
        await _auto._finalize_terminal_state(task_id, my_session=sess)
        async with maker() as db:
            t = await db.get(Task, task_id)
            assert t.status == 'completed', (
                f'A task that never asked a question must complete; '
                f'got {t.status}.'
            )

    async def test_missing_attrs_completes(self, env):
        """No-regression for FakeAgent / non-ClaudeCode sessions: a
        session object without the Q&A attrs (getattr default False)
        completes normally. This is why every existing workflow
        finalizer test is unaffected by the fix."""
        maker, mgr = env
        task_id = await _seed_running_task(maker)
        # Bare session — no _asked_user_question / _tool_use_since_answer.
        mgr._sessions[task_id] = SimpleNamespace(
            _is_error_result=False, _agent_success=True
        )
        sess = mgr.get_session(task_id)
        await _auto._finalize_terminal_state(task_id, my_session=sess)
        async with maker() as db:
            t = await db.get(Task, task_id)
            assert t.status == 'completed'
