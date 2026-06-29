"""Unit tests for the stalled-RUNNING-task reaper.

Covers:
  - Core reaper logic (status transitions, skip rules)
  - Partial result preservation (saves last assistant message)
  - _maybe_bump_updated_at throttle (delta streaming heartbeat)
"""

from datetime import UTC, datetime, timedelta
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

import app.ai.claude_backend_manager as _cb
import app.ai.claude_backend_stream as _stream_mod
import app.database as _db
from app.models.base import Base
from app.models.task import Task
from app.models.task_message import TaskMessage
import app.scheduler.stall_reaper as _reaper
from app.task_states import TaskStatus

pytestmark = pytest.mark.unit


class _FakeManager:
    """Substitute for agent_manager — records stop() calls."""

    def __init__(self):
        self.stopped: list[str] = []
        self.pending: dict[str, dict] = {}

    async def stop(self, task_id: str) -> bool:
        self.stopped.append(task_id)
        return True

    def get_pending_questions(self, task_id: str) -> dict | None:
        return self.pending.get(task_id)


@pytest_asyncio.fixture
async def reaper_env(monkeypatch):
    """In-memory DB + fake agent_manager for reaper tests."""
    engine = create_async_engine(
        'sqlite+aiosqlite://',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
        # AUTOCOMMIT avoids stale-read races across sessions
        # (seed in S1/S2 → reaper reads in S3 must see committed
        # rows including TaskMessage inserted by _add_msg).
        isolation_level='AUTOCOMMIT',
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr(_db, 'async_session', maker)
    monkeypatch.setattr(_reaper, 'async_session', maker)
    monkeypatch.setattr(_stream_mod, 'async_session', maker)
    fake = _FakeManager()
    monkeypatch.setattr(_reaper, 'agent_manager', fake)
    monkeypatch.setattr(_cb, 'agent_manager', fake)
    yield maker, fake


async def _seed(
    maker,
    *,
    status: str,
    minutes_since_update: float,
) -> str:
    """Seed one Task and return its id."""
    task_id = str(uuid.uuid4())
    stale_at = (
        datetime.now(UTC) - timedelta(minutes=minutes_since_update)
    ).isoformat()
    async with maker() as db:
        db.add(
            Task(
                id=task_id,
                created_by='u',
                title='t',
                status=status,
                updated_at=stale_at,
                created_at=stale_at,
            )
        )
        await db.commit()
    return task_id


async def _get(maker, task_id: str) -> Task:
    async with maker() as db:
        return await db.get(Task, task_id)


class TestStallReaper:
    """The reaper flips only RUNNING tasks past the 3-minute cutoff."""

    async def test_stalled_running_task_is_failed(self, reaper_env):
        """RUNNING + stale `updated_at` → status flipped to FAILED."""
        maker, fake = reaper_env
        task_id = await _seed(
            maker, status=TaskStatus.RUNNING, minutes_since_update=10
        )
        await _reaper.reap_stalled_running_tasks()
        t = await _get(maker, task_id)
        assert t.status == TaskStatus.FAILED
        assert t.error_category == 'agent_stream_stalled'
        assert t.completed_at is not None
        assert fake.stopped == [task_id]

    async def test_recently_updated_running_task_is_left_alone(
        self, reaper_env
    ):
        """RUNNING but fresh → reaper does nothing (still ticking)."""
        maker, fake = reaper_env
        task_id = await _seed(
            maker, status=TaskStatus.RUNNING, minutes_since_update=1
        )
        await _reaper.reap_stalled_running_tasks()
        t = await _get(maker, task_id)
        assert t.status == TaskStatus.RUNNING
        assert t.error is None
        assert fake.stopped == []

    async def test_non_running_statuses_are_skipped(self, reaper_env):
        """COMPLETED / FAILED / WAITING / DESIGNING are out of scope.

        DESIGNING has its own plan_reaper. WAITING has its own
        check_waiting_tasks. Terminal statuses are already terminal.
        """
        maker, fake = reaper_env
        ids = {}
        for status in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.WAITING,
            TaskStatus.DESIGNING,
            TaskStatus.PENDING,
        ):
            ids[status] = await _seed(
                maker, status=status, minutes_since_update=30
            )
        await _reaper.reap_stalled_running_tasks()
        for status, tid in ids.items():
            t = await _get(maker, tid)
            assert t.status == status, f'{status} was mutated'
        assert fake.stopped == []

    async def test_multiple_stalled_tasks_in_single_pass(self, reaper_env):
        """Reaper processes all stalled tasks in one invocation."""
        maker, fake = reaper_env
        ids = [
            await _seed(
                maker,
                status=TaskStatus.RUNNING,
                minutes_since_update=20,
            )
            for _ in range(3)
        ]
        await _reaper.reap_stalled_running_tasks()
        for tid in ids:
            t = await _get(maker, tid)
            assert t.status == TaskStatus.FAILED
        assert sorted(fake.stopped) == sorted(ids)

    async def test_running_task_with_pending_question_is_skipped(
        self, reaper_env
    ):
        """RUNNING + stale `updated_at` BUT with an outstanding
        AskUserQuestion is left alone — the agent is legitimately
        blocked on operator input, not stalled upstream.
        """
        maker, fake = reaper_env
        task_id = await _seed(
            maker, status=TaskStatus.RUNNING, minutes_since_update=10
        )
        fake.pending[task_id] = {
            'request_id': 'req-1',
            'questions': [{'question': 'country?'}],
        }
        await _reaper.reap_stalled_running_tasks()
        t = await _get(maker, task_id)
        assert t.status == TaskStatus.RUNNING
        assert t.error is None
        assert fake.stopped == []

    async def test_idempotent_on_repeated_runs(self, reaper_env):
        """Re-running the reaper doesn't re-fail already-FAILED tasks."""
        maker, fake = reaper_env
        task_id = await _seed(
            maker, status=TaskStatus.RUNNING, minutes_since_update=10
        )
        await _reaper.reap_stalled_running_tasks()
        first_completed_at = (await _get(maker, task_id)).completed_at
        await _reaper.reap_stalled_running_tasks()
        t = await _get(maker, task_id)
        # Status should still be FAILED and the timestamp shouldn't
        # have been overwritten on the second pass.
        assert t.status == TaskStatus.FAILED
        assert t.completed_at == first_completed_at
        # agent_manager.stop() should have been called exactly once —
        # the second pass saw no RUNNING task and did nothing.
        assert fake.stopped == [task_id]


async def _add_msg(maker, task_id, role, content, seq):
    """Insert a TaskMessage row."""
    async with maker() as db:
        db.add(
            TaskMessage(
                task_id=task_id,
                role=role,
                content=content,
                seq=seq,
            )
        )
        await db.commit()


class TestStallReaperPartialResult:
    """Verify the reaper preserves partial assistant output
    when force-failing a stalled task.

    This catches the bug where MiniMax's SSE stream drops
    mid-response: the agent's 15000-char report was in
    task_messages but lost on force-fail because no `result`
    event arrived.
    """

    async def test_saves_last_assistant_as_partial_result(self, reaper_env):
        """Stalled task with assistant messages → result preserved."""
        maker, fake = reaper_env
        task_id = await _seed(
            maker, status=TaskStatus.RUNNING, minutes_since_update=10
        )
        await _add_msg(
            maker,
            task_id,
            'assistant',
            'Here is the full ads-tuning report with all 7 '
            'campaigns, per-campaign problems, recommended '
            'actions, and expected outcomes for each.',
            seq=0,
        )

        await _reaper.reap_stalled_running_tasks()

        t = await _get(maker, task_id)
        assert t.status == TaskStatus.FAILED
        assert t.result is not None
        assert '[PARTIAL — stream stalled' in t.result
        assert 'ads-tuning report' in t.result

    async def test_result_event_means_completed_not_failed(self, reaper_env):
        """A recovered `result` event = the deliverable is done.

        The session's main loop finished and only the stop event was
        lost — a transport failure, not a task failure. The task must
        land COMPLETED with the result saved verbatim, never FAILED
        with a "Re-run to retry" banner over finished work.
        """
        maker, fake = reaper_env
        task_id = await _seed(
            maker, status=TaskStatus.RUNNING, minutes_since_update=10
        )
        await _add_msg(
            maker,
            task_id,
            'assistant',
            'Thinking out loud about the campaigns...',
            seq=0,
        )
        await _add_msg(
            maker,
            task_id,
            'result',
            'Final tuning report with detailed '
            'recommendations for all 7 active campaigns '
            'including per-keyword bid adjustments and '
            'negation targets.',
            seq=1,
        )

        await _reaper.reap_stalled_running_tasks()

        t = await _get(maker, task_id)
        assert t.status == TaskStatus.COMPLETED
        assert t.error is None
        assert 'tuning report' in t.result
        assert '[PARTIAL' not in t.result

    async def test_declared_result_means_completed(self, reaper_env):
        """Task.result already set (vibe_seller_set_task_result) and
        no error recorded → the agent declared success before the
        stream died → COMPLETED."""
        maker, fake = reaper_env
        task_id = await _seed(
            maker, status=TaskStatus.RUNNING, minutes_since_update=10
        )
        async with maker() as db:
            t = await db.get(Task, task_id)
            t.result = 'AD_AUDIT_2026-06-05.md — full audit, review ok'
            await db.commit()

        await _reaper.reap_stalled_running_tasks()

        t = await _get(maker, task_id)
        assert t.status == TaskStatus.COMPLETED
        assert t.error is None
        assert t.result == 'AD_AUDIT_2026-06-05.md — full audit, review ok'

    async def test_result_plus_error_still_fails(self, reaper_env):
        """result + error together is the documented partial-output-
        on-failure combination — the error wins, task FAILED."""
        maker, fake = reaper_env
        task_id = await _seed(
            maker, status=TaskStatus.RUNNING, minutes_since_update=10
        )
        async with maker() as db:
            t = await db.get(Task, task_id)
            t.result = 'Partial output before the browser died'
            t.error = 'browser cannot start'
            await db.commit()

        await _reaper.reap_stalled_running_tasks()

        t = await _get(maker, task_id)
        assert t.status == TaskStatus.FAILED

    async def test_no_result_when_only_tool_use_messages(self, reaper_env):
        """Tool-use messages aren't useful as partial results."""
        maker, fake = reaper_env
        task_id = await _seed(
            maker, status=TaskStatus.RUNNING, minutes_since_update=10
        )
        await _add_msg(
            maker,
            task_id,
            'tool_use',
            '{"tool": "Read", "input": {}}',
            seq=0,
        )

        await _reaper.reap_stalled_running_tasks()

        t = await _get(maker, task_id)
        assert t.status == TaskStatus.FAILED
        assert t.result is None

    async def test_skips_short_assistant_messages(self, reaper_env):
        """Messages under 100 chars are too short to preserve."""
        maker, fake = reaper_env
        task_id = await _seed(
            maker, status=TaskStatus.RUNNING, minutes_since_update=10
        )
        await _add_msg(maker, task_id, 'assistant', 'OK', seq=0)

        await _reaper.reap_stalled_running_tasks()

        t = await _get(maker, task_id)
        assert t.status == TaskStatus.FAILED
        assert t.result is None

    async def test_no_overwrite_of_existing_result(self, reaper_env):
        """If task already has a result, the reaper keeps it."""
        maker, fake = reaper_env
        task_id = await _seed(
            maker, status=TaskStatus.RUNNING, minutes_since_update=10
        )
        async with maker() as db:
            t = await db.get(Task, task_id)
            t.result = 'Original result from agent'
            await db.commit()
        await _add_msg(maker, task_id, 'assistant', 'Newer message' * 20, seq=0)

        await _reaper.reap_stalled_running_tasks()

        t = await _get(maker, task_id)
        assert t.result == 'Original result from agent'


class TestMaybeBumpUpdatedAt:
    """Throttled DB heartbeat during delta streaming.

    This catches the bug where _emit_ephemeral never bumps
    updated_at, so slow streaming can false-positive trigger
    the stall reaper.
    """

    async def test_first_call_bumps_immediately(self, reaper_env):
        maker, _ = reaper_env
        task_id = await _seed(
            maker, status=TaskStatus.RUNNING, minutes_since_update=0
        )

        mixin = _stream_mod._StreamMixin()
        mixin.task_id = task_id

        await mixin._maybe_bump_updated_at()

        t = await _get(maker, task_id)
        assert t.updated_at is not None

    async def test_second_call_within_60s_is_noop(self, reaper_env):
        """Throttle: second call <60s after first does no DB write."""
        maker, _ = reaper_env
        task_id = await _seed(
            maker, status=TaskStatus.RUNNING, minutes_since_update=0
        )

        mixin = _stream_mod._StreamMixin()
        mixin.task_id = task_id

        await mixin._maybe_bump_updated_at()
        t1 = await _get(maker, task_id)
        first_ts = t1.updated_at

        await mixin._maybe_bump_updated_at()
        t2 = await _get(maker, task_id)
        assert t2.updated_at == first_ts

    async def test_call_after_60s_bumps_again(self, reaper_env):
        """After the throttle window, a new bump is written."""
        maker, _ = reaper_env
        task_id = await _seed(
            maker, status=TaskStatus.RUNNING, minutes_since_update=0
        )

        mixin = _stream_mod._StreamMixin()
        mixin.task_id = task_id

        await mixin._maybe_bump_updated_at()
        t1 = await _get(maker, task_id)
        first_ts = t1.updated_at

        # Simulate 61s elapsed
        mixin._last_updated_at_bump = datetime.now(UTC) - timedelta(seconds=61)

        await mixin._maybe_bump_updated_at()
        t2 = await _get(maker, task_id)
        assert t2.updated_at != first_ts
