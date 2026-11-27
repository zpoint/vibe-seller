"""Task-end reaping: everything an agent started dies with its task.

Incident 97db66d2's leftovers were DETACHED (``nohup``, ``ssh -f``):
they escaped the process-group stop and carried the task's
``VIBE_TASK_ID`` in their environment. These tests pin the invariant
on the env-scan platforms: a detached child of a task is reaped when
the task ends, and another task's child is NOT (each carries its own
id). The Windows job-object path is proved on native Windows.
"""

import asyncio
import os
import subprocess
import time

import pytest

from app.task_reaper import reap_task_processes

pytestmark = pytest.mark.unit

_linux_only = pytest.mark.skipif(
    not os.path.isdir('/proc'), reason='procfs env scan (Linux/WSL)'
)


def _alive(pid: int) -> bool:
    """Running (not dead, not a zombie the test parent has not
    reaped — a killed child's /proc entry lingers as Z until
    ``wait()``, and that must read as dead here)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        with open(f'/proc/{pid}/stat') as fh:
            return fh.read().rsplit(')', 1)[1].split()[0] != 'Z'
    except (OSError, IndexError):
        return True


def _spawn_detached(env: dict) -> tuple[int, subprocess.Popen]:
    """A child that escapes this process's group, exactly like the
    incident's ``nohup`` / ``ssh -f`` leftovers: ``setsid`` + exec
    (no start_new_session — that would make it a group leader and
    setsid would FORK, handing back a pid that is not the sleeper)."""
    proc = subprocess.Popen(
        ['setsid', 'sh', '-c', 'exec sleep 120'],
        env={**os.environ, **env},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc.pid, proc


def _reap(proc: subprocess.Popen) -> None:
    try:
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        proc.kill()


def _wait_dead(pid: int, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    return not _alive(pid)


@_linux_only
def test_a_detached_child_of_the_task_is_reaped():
    task = 'aa11bb22cc33dd44ee5'
    pid, proc = _spawn_detached({'VIBE_TASK_ID': task})
    try:
        assert _alive(pid), 'detached child did not start'
        killed = asyncio.run(reap_task_processes(task))
        assert killed >= 1
        assert _wait_dead(pid), 'detached child survived its task end'
    finally:
        _reap(proc)


@_linux_only
def test_another_tasks_child_is_not_reaped():
    """The negative control: reaping task A leaves task B's child —
    and an unmarked process — alone."""
    mine = 'bb22cc33dd44ee55ff6'
    other = 'cc33dd44ee55ff66aa7'
    other_pid, other_proc = _spawn_detached({'VIBE_TASK_ID': other})
    plain_pid, plain_proc = _spawn_detached({})
    try:
        assert _alive(other_pid) and _alive(plain_pid)
        asyncio.run(reap_task_processes(mine))
        time.sleep(0.3)
        assert _alive(other_pid), "another task's child was reaped"
        assert _alive(plain_pid), 'an unmarked process was reaped'
    finally:
        _reap(other_proc)
        _reap(plain_proc)


@_linux_only
def test_reaping_is_never_fatal():
    # A bogus task id must not raise — reaping is a backstop.
    assert asyncio.run(reap_task_processes('not-a-real-task-id')) == 0
