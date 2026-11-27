"""The daemon `vibe-seller start` leaves the launcher's console behind.

On Windows, ``start_new_session`` is POSIX-only and silently ignored,
so the daemon used to stay attached to the launcher's console — and a
console-wide control event (Ctrl+C, Ctrl+Break, closing the terminal)
terminates every process on that console, silently: no traceback, no
WER entry. These pin the platform halves of the daemon spawn: Windows
gets its own hidden console + process group, POSIX keeps the new
session. ``--foreground`` / ``--dev`` intentionally stay on the
terminal (Ctrl+C there is how you stop a foreground server).
"""

import argparse
from unittest import mock

import pytest

from app import windows_update
import app.cli as cli

pytestmark = pytest.mark.unit

PORT = 7899
WIN_FLAGS = windows_update.CREATE_NEW_PROCESS_GROUP | (
    windows_update.CREATE_NO_WINDOW
)


def _args(**kw) -> argparse.Namespace:
    base = {
        'host': '127.0.0.1',
        'port': PORT,
        'foreground': False,
        'dev': False,
    }
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    monkeypatch.setenv('VIBE_HOME', str(tmp_path))
    (tmp_path / 'pids').mkdir()
    (tmp_path / 'logs').mkdir()


def _start_daemon(clean_env):
    proc = mock.Mock(pid=4242)
    proc.poll.return_value = None
    with (
        mock.patch.object(cli.subprocess, 'Popen', return_value=proc) as popen,
        mock.patch.object(cli.time, 'sleep'),
        mock.patch.object(cli, '_read_pid', return_value=None),
    ):
        cli._cmd_start(_args())
    return popen


def test_windows_daemon_gets_its_own_console_and_group(clean_env):
    with mock.patch.object(cli.sys, 'platform', 'win32'):
        popen = _start_daemon(clean_env)
    kwargs = popen.call_args.kwargs
    assert kwargs.get('creationflags') == WIN_FLAGS, (
        'the daemon must get a hidden console of its own and be a '
        'process-group root, or a console-wide event on the launcher '
        'kills it silently'
    )
    assert 'start_new_session' not in kwargs


def test_posix_daemon_still_gets_a_new_session(clean_env):
    with mock.patch.object(cli.sys, 'platform', 'linux'):
        popen = _start_daemon(clean_env)
    kwargs = popen.call_args.kwargs
    assert kwargs.get('start_new_session') is True
    assert 'creationflags' not in kwargs
