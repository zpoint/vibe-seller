"""Regression guard: `_stream_output` must heartbeat while the
subprocess is alive but upstream is silent.

Some provider transports (observed with deepseek via claude-code)
emit ZERO stream deltas during tool-input composition: a multi-KB
Write call's ``content`` argument is generated server-side and the
CLI only forwards the complete tool_use at the end. Composition
can take 5+ minutes; with no events arriving, ``Task.updated_at``
goes stale and the stall reaper kills a healthy agent
mid-generation (task 73032910 — 32 KB audit-report Write killed
twice before this fix landed).

The handler-level fix (``input_json_delta`` -> ``_maybe_bump_updated_at``)
is correct in principle but vacuously useful when the transport
emits no deltas at all. Subprocess-alive IS the signal we have:
``self._proc.stdout.readline()`` blocks for the entire composition
window because the CLI is itself blocked on the upstream HTTP
response. Wrapping ``readline`` in a 60s ``asyncio.wait_for`` and
bumping the heartbeat on every timeout (when ``returncode is None``)
gives the stall reaper the right picture: subprocess alive ->
agent healthy.

This test directly exercises the wait-for/timeout/heartbeat path
inside ``_stream_output`` without spinning up a real subprocess.
"""

import asyncio
from datetime import UTC, datetime, timedelta

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
from app.task_states import TaskStatus

pytestmark = pytest.mark.unit


_TASK_ID = 'task-subprocess-alive-hb'


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


class _FakeStdout:
    """Minimal asyncio.StreamReader stand-in.

    `readline()` is awaited inside `asyncio.wait_for(..., timeout=60)`
    in `_stream_output`. We just need a coroutine that never resolves
    by itself so the timeout fires; then on the second pass we return
    EOF (b'') so the loop exits cleanly.
    """

    def __init__(self):
        self._calls = 0

    async def readline(self):
        self._calls += 1
        if self._calls == 1:
            # Block forever — wait_for will TimeoutError.
            await asyncio.Event().wait()
        return b''  # EOF on the next iteration → exit loop


class _FakeProc:
    def __init__(self):
        self.stdout = _FakeStdout()
        self.stdin = None
        self.returncode = None  # subprocess alive

    async def wait(self):
        return 0


class TestSubprocessAliveHeartbeat:
    async def test_readline_timeout_with_alive_subprocess_bumps_heartbeat(
        self, _db, monkeypatch
    ):
        """When `readline` times out and the subprocess is still alive,
        `_stream_output` must bump `Task.updated_at` and keep waiting
        — not exit the loop and not let the reaper see a stale task.

        Speeds the inner timeout from 60 s to 0.05 s by monkeypatching
        the module-level constant `_READLINE_HEARTBEAT_TIMEOUT_S`
        (NOT `asyncio.wait_for` — patching that globally would race
        with the test's own outer wait_for guard).
        """
        monkeypatch.setattr('app.ai.claude_backend_stream.async_session', _db)

        # Force the readline-heartbeat timeout to fire quickly so the
        # test runs in milliseconds rather than waiting 60 seconds.
        # Patching the module-level constant — NOT `asyncio.wait_for`
        # globally, which would also affect the test's own wait_for
        # calls and create a race.
        monkeypatch.setattr(
            'app.ai.claude_backend_stream._READLINE_HEARTBEAT_TIMEOUT_S',
            0.05,
        )

        session = AgentSession(task_id=_TASK_ID, prompt='hi', mode='auto')
        session._proc = _FakeProc()  # type: ignore[assignment]

        async with _db() as db:
            before = (await db.get(Task, _TASK_ID)).updated_at

        # Run the stream loop. First readline → TimeoutError →
        # heartbeat bump → continue. Second readline → b'' → EOF →
        # loop exits cleanly.
        #
        # Outer wait_for guards against the unfixed code path:
        # without the readline timeout, `_stream_output` blocks on
        # the never-resolving readline forever and the test would
        # hang. Two seconds is more than enough — the inner timeout
        # is patched to 50 ms above.
        await asyncio.wait_for(session._stream_output(), timeout=2.0)

        async with _db() as db:
            after = (await db.get(Task, _TASK_ID)).updated_at

        assert after > before, (
            'Subprocess-alive readline timeout must bump '
            f'Task.updated_at; before={before} after={after}'
        )
