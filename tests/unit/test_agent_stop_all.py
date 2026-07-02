"""Unit test for ClaudeCodeBackend.stop_all().

Pins the shutdown contract that prevents orphaned task agents: on
server shutdown, every RUNNING session is stopped (each session's
stop() killpg's its `claude -p` subtree), idle/finished sessions are
left alone, and one session raising never blocks the rest. Orphaned
agents keep hitting `browser/start` on the next server and thrash the
shared Ziniao client, so this contract is load-bearing.
"""

from unittest import mock

import pytest

from app.ai.claude_backend_manager import ClaudeCodeBackend

pytestmark = pytest.mark.unit


def _session(running: bool):
    s = mock.MagicMock()
    s.running = running
    s.stop = mock.AsyncMock()
    return s


@pytest.mark.asyncio
async def test_stop_all_stops_only_running_sessions():
    mgr = ClaudeCodeBackend()
    run1, run2, idle = _session(True), _session(True), _session(False)
    mgr._sessions = {'t1': run1, 't2': run2, 't3': idle}

    stopped = await mgr.stop_all()

    assert stopped == 2
    run1.stop.assert_awaited_once()
    run2.stop.assert_awaited_once()
    idle.stop.assert_not_awaited()


@pytest.mark.asyncio
async def test_stop_all_is_resilient_to_one_failure():
    mgr = ClaudeCodeBackend()
    good, bad = _session(True), _session(True)
    bad.stop = mock.AsyncMock(side_effect=RuntimeError('boom'))
    mgr._sessions = {'good': good, 'bad': bad}

    # Must not raise even though one session's stop() blows up.
    stopped = await mgr.stop_all()

    assert stopped == 2
    good.stop.assert_awaited_once()
    bad.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_all_no_running_sessions_returns_zero():
    mgr = ClaudeCodeBackend()
    mgr._sessions = {'t1': _session(False)}
    assert await mgr.stop_all() == 0
