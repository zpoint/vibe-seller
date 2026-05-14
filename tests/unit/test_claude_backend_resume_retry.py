"""Unit tests for ``ClaudeCodeBackend.retry_without_resume``.

The retry decision now lives in the orchestrator
(``app.task_session_lifecycle._maybe_retry_without_resume``) — these
tests cover the manager-side mechanism it calls into:

- ``retry_without_resume(task_id)`` creates a fresh ``AgentSession``
  with ``resume_session_id=None`` that inherits every config arg
  from the prior session (prompt, mode, profile, history, etc.) so
  the orchestrator doesn't have to re-thread them.
- Returns False (and does NOT acquire a semaphore slot) if no prior
  session exists for the task.
- Once the retry session emits a new session_id, the manager
  persists it back to ``task.session_id`` exactly like the original
  ``run`` flow.
- Semaphore accounting is balanced across the retry — no leak.

Lifecycle integration (orchestrator-owned wait + retry + finalize)
is covered separately in ``tests/unit/test_resume_retry.py`` and
``tests/workflow/test_wf_resume_retry.py``.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.ai import claude_backend_manager as mgr_mod
from app.ai.claude_backend_manager import ClaudeCodeBackend
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
                id='task-retry',
                title='retry test',
                status='running',
                session_id='root-session-id',
                created_by='00000000-0000-0000-0000-000000000001',
                created_at='2026-01-01T00:00:00',
                updated_at='2026-01-01T00:00:00',
            )
        )
        await db.commit()
    yield maker
    await engine.dispose()


class _FakeSession:
    """Stand-in for ``AgentSession`` — records args, runs a scripted
    completion, surfaces only the attributes the manager touches.
    """

    instances: list['_FakeSession'] = []

    def __init__(self, task_id, prompt, **kwargs):
        self.task_id = task_id
        self.prompt = prompt
        self.store_slug = kwargs.get('store_slug')
        self.system_prompt_extra = kwargs.get('system_prompt_extra', '')
        self.mode = kwargs.get('mode', 'execute')
        self.profile_id = kwargs.get('profile_id', 'default')
        self.message_history = kwargs.get('message_history') or []
        self.no_store = kwargs.get('no_store', False)
        self.auto_approve_plan = kwargs.get('auto_approve_plan', False)
        self.task_dir = kwargs.get('task_dir')
        self.skip_reflection = kwargs.get('skip_reflection', False)
        self.resume_session_id: str | None = None
        self.session_id: str | None = None
        self._task: asyncio.Task | None = None
        self._proc = MagicMock()
        self._proc.returncode = None
        self._result_text = ''
        self.running = True
        self.done = asyncio.Event()
        self.plan_saved_event = asyncio.Event()
        _FakeSession.instances.append(self)

    async def start(self):
        self._task = asyncio.create_task(self._body())

    async def _body(self):
        await asyncio.sleep(0)
        self._proc.returncode = getattr(self, '_scripted_rc', 0)
        self.session_id = getattr(self, '_scripted_session_id', None)
        self._result_text = getattr(self, '_scripted_result', '')
        self.done.set()


def _patch_backend(monkeypatch, _db):
    monkeypatch.setattr(mgr_mod, 'AgentSession', _FakeSession)
    monkeypatch.setattr(mgr_mod, 'async_session', _db)
    monkeypatch.setattr(
        mgr_mod,
        'workspace_manager',
        MagicMock(prepare_task_workspace=AsyncMock(return_value=None)),
    )


class TestRetryWithoutResume:
    async def test_returns_false_when_no_prior_session(self, _db, monkeypatch):
        """Without a prior run there's nothing to inherit args from
        — the manager should refuse and not acquire a semaphore slot.
        """
        _FakeSession.instances = []
        _patch_backend(monkeypatch, _db)
        backend = ClaudeCodeBackend()

        ok = await backend.retry_without_resume('task-retry')
        assert ok is False
        assert _FakeSession.instances == []
        assert backend._in_flight == 0

    async def test_inherits_all_args_and_clears_resume_id(
        self, _db, monkeypatch
    ):
        """A retry session inherits every config arg from the prior
        session but starts with no ``resume_session_id`` so the new
        subprocess does NOT see ``--resume <stale>``.
        """
        _FakeSession.instances = []
        _patch_backend(monkeypatch, _db)
        backend = ClaudeCodeBackend()

        # Stage 1: prime a prior session via run() with resume=True.
        # Script it as a clean exit so no implicit retry happens.
        orig_init = _FakeSession.__init__

        def patched_init(self, *a, **kw):
            orig_init(self, *a, **kw)
            self._scripted_rc = 0
            self._scripted_result = 'first'
            self._scripted_session_id = 'root-session-id'

        monkeypatch.setattr(_FakeSession, '__init__', patched_init)

        started = await backend.run(
            'task-retry',
            'original prompt',
            store_slug='store-x',
            system_extra='extra',
            mode='auto',
            profile_id='p1',
            no_store=False,
            resume=True,
            auto_approve_plan=True,
            skip_reflection=True,
        )
        assert started is True

        # Drain the prior session to free the semaphore.
        for _ in range(100):
            if backend._in_flight == 0:
                break
            await asyncio.sleep(0.01)
        assert len(_FakeSession.instances) == 1
        prior = _FakeSession.instances[0]
        assert prior.resume_session_id == 'root-session-id'

        # Stage 2: orchestrator decides to retry-without-resume.
        # Re-script future instances with a fresh id.
        def retry_init(self, *a, **kw):
            orig_init(self, *a, **kw)
            self._scripted_rc = 0
            self._scripted_result = 'fresh'
            self._scripted_session_id = 'fresh-retry-sid'

        monkeypatch.setattr(_FakeSession, '__init__', retry_init)

        ok = await backend.retry_without_resume('task-retry')
        assert ok is True
        # Drain retry's _release_on_done.
        for _ in range(100):
            if backend._in_flight == 0:
                break
            await asyncio.sleep(0.01)

        assert len(_FakeSession.instances) == 2
        retry = _FakeSession.instances[1]
        assert retry.resume_session_id is None, (
            'Retry must NOT carry the rejected --resume id'
        )
        # Args inherited 1:1 from prior.
        assert retry.prompt == prior.prompt == 'original prompt'
        assert retry.store_slug == prior.store_slug == 'store-x'
        assert retry.mode == prior.mode == 'auto'
        assert retry.profile_id == prior.profile_id == 'p1'
        assert retry.system_prompt_extra == prior.system_prompt_extra
        assert retry.auto_approve_plan == prior.auto_approve_plan is True
        assert retry.skip_reflection == prior.skip_reflection is True
        assert retry.task_dir == prior.task_dir
        # Registry now holds the retry, not the prior.
        assert backend.get_session('task-retry') is retry

    async def test_persists_retry_session_id(self, _db, monkeypatch):
        """Once the retry session emits its own session_id, the
        manager writes it back to ``task.session_id`` (the orchestrator
        already cleared the stale one before this call).
        """
        _FakeSession.instances = []
        _patch_backend(monkeypatch, _db)
        backend = ClaudeCodeBackend()

        orig_init = _FakeSession.__init__

        def patched_init(self, *a, **kw):
            orig_init(self, *a, **kw)
            self._scripted_rc = 0
            self._scripted_result = 'first'
            self._scripted_session_id = 'old-sid'

        monkeypatch.setattr(_FakeSession, '__init__', patched_init)
        await backend.run('task-retry', 'prompt', resume=True)
        for _ in range(100):
            if backend._in_flight == 0:
                break
            await asyncio.sleep(0.01)

        # Simulate the orchestrator clearing the stale id before
        # asking the manager to retry.
        async with _db() as db:
            t = await db.get(Task, 'task-retry')
            t.session_id = None
            await db.commit()

        def retry_init(self, *a, **kw):
            orig_init(self, *a, **kw)
            self._scripted_rc = 0
            self._scripted_result = 'fresh'
            self._scripted_session_id = 'fresh-retry-sid'

        monkeypatch.setattr(_FakeSession, '__init__', retry_init)
        await backend.retry_without_resume('task-retry')
        for _ in range(100):
            if backend._in_flight == 0:
                break
            await asyncio.sleep(0.01)

        async with _db() as db:
            t = await db.get(Task, 'task-retry')
            assert t.session_id == 'fresh-retry-sid'
        # Semaphore balanced across both runs.
        assert backend._in_flight == 0
