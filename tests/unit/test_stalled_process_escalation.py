"""A process that stalls after its final result may not wedge the task.

Brief #15 problem 2 (CI task 030d928e): the CLI emitted its final
result and then never exited — the documented GLM stall-after-result.
The stream loop's stdout-EOF path closed stdin INLINE (bypassing
``_close_stdin``, so the post-close clock was never armed) and then
``await self._proc.wait()`` with NO bound — the one place the module's
own contract ("stdin closed + grace elapsed + alive = force kill") was
not enforced. The task sat RUNNING with no rc and no terminal status
until the CI step was killed, while an identical task whose CLI exited
(2c6ff324) completed in 8 seconds.

These tests pin the contract's two halves: the bounded wait escalates,
and both stream loops route their EOF path through it.
"""

import asyncio
from pathlib import Path

import pytest

from app.ai.claude_backend import AgentSession

pytestmark = pytest.mark.unit


class _StallingProc:
    """A subprocess that says nothing more and never exits until killed."""

    def __init__(self):
        self.returncode = None
        self._done = asyncio.Event()

    async def wait(self):
        await self._done.wait()
        return self.returncode

    def kill(self, rc):
        self.returncode = rc
        self._done.set()


def _session_with(monkeypatch, proc, grace):
    """A real AgentSession whose only faked surface is the process."""
    monkeypatch.setattr(
        'app.ai.claude_backend_turns._POST_CLOSE_KILL_GRACE_S', grace
    )
    session = AgentSession(task_id='stall-030d928e', prompt='t', mode='auto')
    session._proc = proc
    forced = []

    async def _force():
        forced.append(True)
        proc.kill(-9)

    monkeypatch.setattr(session, '_force_kill', _force)
    return session, forced


def test_a_stalling_process_is_killed_not_awaited_forever(monkeypatch):
    proc = _StallingProc()
    session, forced = _session_with(monkeypatch, proc, 0.05)

    rc = asyncio.run(session._wait_process_exit_after_close())

    assert forced, 'the grace elapsed without an escalation'
    assert rc == -9


def test_a_process_that_exits_is_not_killed(monkeypatch):
    proc = _StallingProc()
    proc.kill(0)  # exits immediately
    session, forced = _session_with(monkeypatch, proc, 5.0)

    rc = asyncio.run(session._wait_process_exit_after_close())

    assert rc == 0
    assert not forced


def test_both_stream_loops_wait_bounded_after_stdout_eof():
    """The EOF path must not carry a bare ``await self._proc.wait()``
    (the old, unbounded shape) — it routes through the bounded helper.
    Fails on the pre-fix code (negative control): both files used to
    wait unboundedly at exactly this point.
    """
    for name in (
        'app/ai/claude_backend_stream.py',
        'app/ai/workspace_assistant.py',
    ):
        src = Path(name).read_text(encoding='utf-8')
        assert '_wait_process_exit_after_close()' in src, name
        assert 'await self._proc.wait()' not in src, (
            f'{name} still waits unboundedly on the process'
        )
