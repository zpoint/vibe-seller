"""A server never keeps serving code the disk no longer has.

``app.upgrade_restart`` restarts the server onto an upgraded install —
but only once no task agent is live, and without starting new work
while it waits.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from app import upgrade_restart
from app.ai.claude_backend_manager import ClaudeCodeBackend
from app.scheduler.task_queue import TaskQueueScheduler

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    monkeypatch.setattr(upgrade_restart, '_pending_version', None)
    monkeypatch.setattr(upgrade_restart, '_restart_requested', False)
    monkeypatch.setattr(upgrade_restart, '_drain_started_at', None)
    monkeypatch.setattr(upgrade_restart, 'get_version', lambda: '1.0.0')


class _Agents:
    def __init__(self, live):
        self.live = live

    def live_agent_count(self):
        return self.live


def _check(on_disk, live):
    queue = MagicMock()
    with (
        patch.object(
            upgrade_restart, 'installed_version', return_value=on_disk
        ),
        patch.object(upgrade_restart.os, 'kill') as kill,
    ):
        done = asyncio.run(upgrade_restart.check_once(queue, _Agents(live)))
    return done, queue, kill


def test_an_idle_stale_server_restarts():
    done, queue, kill = _check('2.0.0', live=0)
    assert done is True
    queue.pause_dispatch.assert_called_once()
    kill.assert_called_once()  # graceful SIGTERM to itself
    assert upgrade_restart.pending_version() == '2.0.0'


def test_a_busy_stale_server_drains_and_waits():
    done, queue, kill = _check('2.0.0', live=2)
    assert done is False
    queue.pause_dispatch.assert_called_once()  # nothing new starts
    kill.assert_not_called()  # live agents are never killed
    assert upgrade_restart.pending_version() == '2.0.0'


def test_it_restarts_once_the_last_agent_finishes():
    _check('2.0.0', live=1)
    done, _queue, kill = _check('2.0.0', live=0)
    assert done is True
    kill.assert_called_once()


@pytest.mark.parametrize('on_disk', ['1.0.0', None])
def test_an_up_to_date_server_does_nothing(on_disk):
    done, queue, kill = _check(on_disk, live=0)
    assert done is False
    queue.pause_dispatch.assert_not_called()
    kill.assert_not_called()
    assert upgrade_restart.pending_version() is None


def test_reexec_is_the_same_command_line(monkeypatch):
    monkeypatch.setattr(upgrade_restart.sys, 'executable', '/venv/bin/python')
    monkeypatch.setattr(
        upgrade_restart.sys,
        'orig_argv',
        ['python3', '-m', 'uvicorn', 'app.main:app', '--port', '7777'],
    )
    monkeypatch.setattr(upgrade_restart, '_restart_requested', True)
    with patch.object(upgrade_restart.os, 'execv') as execv:
        upgrade_restart.reexec_if_requested()
    execv.assert_called_once_with(
        '/venv/bin/python',
        [
            '/venv/bin/python',
            '-m',
            'uvicorn',
            'app.main:app',
            '--port',
            '7777',
        ],
    )


def test_an_ordinary_shutdown_does_not_reexec():
    with patch.object(upgrade_restart.os, 'execv') as execv:
        upgrade_restart.reexec_if_requested()
    execv.assert_not_called()


def test_not_on_windows(monkeypatch):
    monkeypatch.setattr(upgrade_restart.sys, 'platform', 'win32')
    assert upgrade_restart.supported() is False


def test_a_paused_queue_dispatches_nothing():
    q = TaskQueueScheduler()
    q._queues[None] = ['t1']
    q.pause_dispatch()
    with patch.object(q, '_dispatch') as dispatch:
        asyncio.run(q._tick())
    dispatch.assert_not_called()
    assert q._queues[None] == ['t1']  # still queued for the next boot


def test_live_agents_are_exactly_what_a_shutdown_stops():
    backend = ClaudeCodeBackend()

    def session(running, returncode):
        s = MagicMock()
        s.running = running
        s._proc = None if returncode == 'none' else MagicMock()
        if s._proc is not None:
            s._proc.returncode = returncode
        return s

    backend._sessions = {
        'live': session(True, None),
        'closing': session(False, None),  # terminator fired, process alive
        'exited': session(False, 0),
        'never-spawned': session(False, 'none'),
    }
    assert backend.live_agent_count() == 2
    assert {tid for tid, _ in backend._live_sessions()} == {'live', 'closing'}


def test_the_restart_is_reported(monkeypatch):
    events = []
    monkeypatch.setattr(
        upgrade_restart.telemetry,
        'send',
        lambda e, p=None: events.append((e, dict(p or {}))),
    )
    _check('2.0.0', live=1)
    _check('2.0.0', live=0)
    assert events == [
        (
            'upgrade_restarted',
            {
                'from_version': '1.0.0',
                'to_version': '2.0.0',
                'drain_wait_bucket': '<30s',
            },
        )
    ]


def test_request_restart_is_idempotent_and_names_the_target(monkeypatch):
    # Review SHOULD 5: the auto-update watcher and check_once can both
    # see the same disk change. The first caller owns the restart; a
    # second is a no-op (one event, one SIGTERM), and the target is
    # recorded so the log line / event never says None.
    events = []
    monkeypatch.setattr(
        upgrade_restart.telemetry,
        'send',
        lambda e, p=None: events.append((e, dict(p or {}))),
    )
    with patch.object(upgrade_restart.os, 'kill') as kill:
        upgrade_restart.request_restart('3.1.0')
        upgrade_restart.request_restart('3.1.0')
    assert kill.call_count == 1
    assert upgrade_restart.pending_version() == '3.1.0'
    assert len(events) == 1
    assert events[0][1]['to_version'] == '3.1.0'
