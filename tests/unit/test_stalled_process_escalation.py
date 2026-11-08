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


class _FakeStdin:
    def close(self):
        pass


class _StallAfterResultProc:
    """030d928e's shape: emits its final result, then stdout hits EOF
    while the process never exits."""

    returncode = None

    def __init__(self, lines):
        self._lines = list(lines)
        self.stdin = _FakeStdin()
        self.stdout = self
        self._done = asyncio.Event()

    async def readline(self):
        if self._lines:
            return self._lines.pop(0).encode() + b'\n'
        return b''  # EOF — the CLI is done talking but never exits

    async def wait(self):
        await self._done.wait()
        return self.returncode

    def kill(self, rc):
        self.returncode = rc
        self._done.set()


def _capture_session(monkeypatch, proc):
    session = AgentSession(task_id='shape-030d928e', prompt='t', mode='execute')
    session._proc = proc
    sent = []

    async def _emit(role, content):
        sent.append((role, content))

    monkeypatch.setattr(session, '_emit_message', _emit)

    async def _force():
        proc.kill(-9)

    monkeypatch.setattr(session, '_force_kill', _force)
    monkeypatch.setattr(session, '_maybe_bump_updated_at', _async_noop)
    monkeypatch.setattr(
        'app.ai.claude_backend_turns._POST_CLOSE_KILL_GRACE_S', 0.05
    )
    monkeypatch.setattr(
        'app.ai.claude_backend_stream.accumulate_run_usage',
        _async_noop,
    )
    return session, sent


async def _async_noop(*_a, **_kw):
    return None


def test_a_stalled_cli_after_a_clean_result_ends_the_task_clean(
    monkeypatch,
):
    """Brief #15b's requirement: the delivered result wins. The reap
    is cleanup — the task must NOT come out FAILED."""
    result_event = (
        '{"type": "result", "subtype": "success", "is_error": false,'
        ' "result": "the answer", "stop_reason": "end_turn"}'
    )
    proc = _StallAfterResultProc([result_event])
    session, sent = _capture_session(monkeypatch, proc)

    asyncio.run(session._stream_output())

    assert session._result_text == 'the answer'
    assert session._escalation_reaped, 'the escalation never fired'
    assert session._is_error_result is False, (
        'a reap after a clean result must not be a task error'
    )
    cleanup = [m for r, m in sent if 'cleanup' in m]
    assert cleanup, [m for _, m in sent]
    assert not [m for r, m in sent if m.startswith('Agent exited')]


def test_a_stalled_cli_with_no_result_still_fails(monkeypatch):
    proc = _StallAfterResultProc([])  # dies silent
    session, sent = _capture_session(monkeypatch, proc)

    asyncio.run(session._stream_output())

    assert session._is_error_result is True
    assert [m for r, m in sent if 'Agent exited with code' in m]


def test_a_reap_after_an_error_result_still_fails(monkeypatch):
    result_event = (
        '{"type": "result", "subtype": "success", "is_error": true,'
        ' "result": "boom", "stop_reason": "end_turn"}'
    )
    proc = _StallAfterResultProc([result_event])
    session, _sent = _capture_session(monkeypatch, proc)

    asyncio.run(session._stream_output())

    assert session._is_error_result is True


def test_a_crash_without_a_reap_still_fails(monkeypatch):
    """A process that dies on its own (rc=-11) is a task error even
    after a clean result — only OUR post-close reap is cleanup."""
    session = AgentSession(task_id='crash-11', prompt='t', mode='execute')
    session._turn_result_seen = True
    session._last_result_is_error = False
    sent = []

    async def _emit(_role, content):
        sent.append(content)

    monkeypatch.setattr(session, '_emit_message', _emit)
    asyncio.run(session._apply_exit_code(-11))
    assert session._is_error_result is True


def test_both_loops_fold_their_exit_through_one_rule():
    """Source pin (negative control on 090c6af): both loops carried a
    bare ``if return_code != 0: self._is_error_result = True``. One
    rule now — the shared _apply_exit_code."""
    for name in (
        'app/ai/claude_backend_stream.py',
        'app/ai/workspace_assistant.py',
    ):
        src = Path(name).read_text(encoding='utf-8')
        assert 'await self._apply_exit_code(return_code)' in src, name
        # the exit-code fold (and its message) lives only in the shared
        # rule now — not in the loops
        assert 'Agent exited with code' not in src, (
            f'{name} still folds the exit code itself'
        )
