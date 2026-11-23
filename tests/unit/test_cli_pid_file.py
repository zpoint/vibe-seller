"""A pid file names one server, not whatever holds that PID later.

`vibe-seller start` writes ``backend_<port>.pid`` about two seconds
after spawning the daemon. A release that dies at boot can outlive that
check, leaving the file behind, and Windows gives the dead PID to the
next new process within seconds — on Windows installer leg s5c, very
likely the `vibe-seller start` that was bringing the old version back,
which then refused with "already running" and left the machine serving
nothing. These pin that only the process the file was written for
counts as the running server.
"""

import os
import subprocess
import sys
from unittest import mock

import psutil
import pytest

from app import platform
import app.cli as cli

pytestmark = pytest.mark.unit

PORT = 7898


@pytest.fixture
def pid_file(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_HOME', str(tmp_path))
    (tmp_path / 'pids').mkdir()
    return tmp_path / 'pids' / f'backend_{PORT}.pid'


def _write(path, pid: int, written_at: float) -> None:
    path.write_text(str(pid))
    os.utime(path, (written_at, written_at))


def test_a_pid_taken_by_a_newer_process_is_not_the_server(pid_file):
    me = psutil.Process()
    # The file is older than the process now holding its PID: whatever
    # it was written for has died and the PID was handed on.
    _write(pid_file, me.pid, me.create_time() - 60)
    assert cli._read_pid(PORT) is None
    assert not pid_file.exists(), 'a stale pid file is left behind'


def test_the_process_the_file_was_written_for_is_the_server(pid_file):
    me = psutil.Process()
    # What `start` does: spawn, then write the file ~2 s later.
    _write(pid_file, me.pid, me.create_time() + 2)
    assert cli._read_pid(PORT) == me.pid
    assert pid_file.exists()


def test_a_dead_pid_is_not_the_server(pid_file):
    child = subprocess.Popen([sys.executable, '-c', 'pass'])
    child.wait()
    _write(pid_file, child.pid, psutil.Process().create_time() + 2)
    assert cli._read_pid(PORT) is None
    assert not pid_file.exists()


def test_a_process_we_may_not_inspect_is_someone_elses():
    # What we record runs as this user; a PID we are denied is not it.
    with mock.patch.object(
        platform.psutil, 'Process', side_effect=psutil.AccessDenied(1)
    ):
        assert platform.process_started_after(1, 0.0) is True


def test_a_process_that_is_gone_leaves_the_answer_to_existence():
    with mock.patch.object(
        platform.psutil, 'Process', side_effect=psutil.NoSuchProcess(1)
    ):
        assert platform.process_started_after(1, 0.0) is False
