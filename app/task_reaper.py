"""Task-end reaping: everything an agent started dies with its task.

Incident 97db66d2: a task agent opened two ``ssh -f -N -L`` tunnels
and ``nohup``'d a CDP bridge on 9222; "Stopped by user" killed the
agent's process group, but ``nohup`` and ``ssh -f`` escape it, and all
three leftovers kept running — every one of them carried the task's
``VIBE_TASK_ID`` in its environment. The Mac addendum found the same
for a detached ``browser_harness.daemon`` that survived a scoped stop.

So on ANY task stop or end, every process whose environment carries
that task's ``VIBE_TASK_ID`` is reaped, however it was detached:

- **Linux/WSL**: scan ``/proc/*/environ``.
- **macOS**: one ``ps eww -axo pid=,command=`` call, whose output
  appends each process's environment to its command line.
- **Windows**: there is no readable per-process environment, so the
  agent process is placed in a JOB OBJECT at spawn (suspended →
  assign → resume, so no grandchild can be born outside it; the job
  does not allow breakaway, and the schtasks / WMI create forms a
  determined agent would need are separately denied by
  :mod:`app.ai.vendor_guard`) and ``TerminateJobObject`` tears the
  whole job — detached children included. The job has NO
  kill-on-close: the server dying closes the handle WITHOUT
  terminating, so a WAITING task's browser daemon survives a server
  restart. The vendor kernel and winchrome's Task-Scheduler Chrome
  are never in the job — they are the server's/adapter's children,
  not the agent's — and survive task ends by design.

Detached browser-use daemons are caught twice: by the environment
scan, and by their pid files (``BH_RUNTIME_DIR/bu-<name>.pid``, the
name carrying the task's 8-char prefix — the same identity
:mod:`app.browser.daemon_reaper` uses).

What reaping must NOT kill, and doesn't: a WAITING task's daemon
(reaping runs only on terminal states and user stops, never on the
WAITING park), the task's own background waits while it is live, and
the shared workplace-CLI login state (server-side processes carry no
``VIBE_TASK_ID``).
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import subprocess
import sys

import psutil

from app.browser.bh_daemons import iter_pidfiles, unlink_quiet
from app.browser.daemon_reaper import task_prefix_for_bu_name
from app.browser.process_utils import kill_with_escalation
from app.platform import IS_WINDOWS

logger = logging.getLogger(__name__)

_ENV_MARK = 'VIBE_TASK_ID='


def _scan_env_pids(task_id: str) -> list[int]:
    """PIDs whose environment carries this task's ``VIBE_TASK_ID``."""
    if sys.platform == 'darwin':
        return _scan_env_pids_mac(task_id)
    return _scan_env_pids_procfs(task_id)


def _scan_env_pids_procfs(task_id: str) -> list[int]:
    """Linux/WSL: read ``/proc/<pid>/environ`` directly."""
    mark = (_ENV_MARK + task_id).encode()
    pids: list[int] = []
    mine = os.getpid()
    try:
        entries = os.listdir('/proc')
    except OSError:
        return pids
    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid == mine:
            continue
        try:
            with open(f'/proc/{pid}/environ', 'rb') as fh:
                env = fh.read()
        except OSError:
            continue  # gone, another user's, or not a process dir
        if mark in env.split(b'\0'):
            pids.append(pid)
    return pids


def _scan_env_pids_mac(task_id: str) -> list[int]:
    """macOS: ``ps eww -axo pid=,command=`` appends each process's
    environment to its command line — one call for the whole table."""
    mark = _ENV_MARK + task_id
    try:
        out = subprocess.run(
            ['ps', 'eww', '-axo', 'pid=,command='],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    pids: list[int] = []
    mine = os.getpid()
    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        pid = int(parts[0])
        if pid != mine and mark in parts[1]:
            pids.append(pid)
    return pids


def _daemon_pidfiles(task_id: str):
    """This task's browser-use daemon (pid, pidfile, sock) triples."""
    prefix = task_id[:8]
    found = []
    for bu_name, pid, pf, sock in iter_pidfiles():
        if task_prefix_for_bu_name(bu_name) == prefix:
            found.append((pid, pf, sock))
    return found


async def reap_task_processes(task_id: str) -> int:
    """Reap every process this task's agent left behind.

    Returns the number of kills signalled. Never raises: reaping is a
    backstop, and a failure here must not take down the pipeline that
    owns the task's terminal transition.
    """
    killed = 0
    try:
        if IS_WINDOWS:
            killed += _terminate_windows_jobs(task_id)
        pids = await asyncio.to_thread(_scan_env_pids, task_id)
        daemon_files = await asyncio.to_thread(_daemon_pidfiles, task_id)
        pids.extend(pid for pid, _pf, _sock in daemon_files if pid)
        pids = [p for p in dict.fromkeys(pids) if p != os.getpid()]
        if pids:
            results = await asyncio.gather(
                *(kill_with_escalation(p) for p in pids),
                return_exceptions=True,
            )
            for r in results:
                if isinstance(r, Exception):
                    logger.warning(
                        'Reaping a process of task %s failed: %r',
                        task_id[:8],
                        r,
                    )
                elif r:
                    killed += 1
            for _pid, pf, sock in daemon_files:
                unlink_quiet(pf, sock)
        if killed:
            logger.info(
                'Reaped %d leftover process(es) for ended task %s',
                killed,
                task_id[:8],
            )
    except Exception:
        logger.warning('Task reaping failed for %s', task_id, exc_info=True)
    return killed


# ── Windows job objects ─────────────────────────────────────────────
# ctypes bindings; every entry is a no-op on other platforms so the
# reaper's callers stay platform-free.

_JOB_OBJECT_ASSIGNED_PROCESS = 9
_PROCESS_ALL_ACCESS = 0x1F0FFF
# Deliberately NOT set on the job (see module docstring):
#   JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000 — would kill a WAITING
#   task's daemon when the server dies.
#   JOB_OBJECT_LIMIT_BREAKAWAY_ON_OK = 0x800 — would let children
#   escape with CREATE_BREAKAWAY_FROM_JOB.

_WIN_JOBS: dict[str, list[int]] = {}


class _IoCounters(ctypes.Structure):
    _fields_ = [
        (n, ctypes.c_uint64)
        for n in (
            'ReadOperationCount',
            'WriteOperationCount',
            'OtherOperationCount',
            'ReadTransferCount',
            'WriteTransferCount',
            'OtherTransferCount',
        )
    ]


class _JobObjectBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ('PerProcessUserTimeLimit', ctypes.c_int64),
        ('PerJobUserTimeLimit', ctypes.c_int64),
        ('LimitFlags', ctypes.c_uint32),
        ('MinimumWorkingSetSize', ctypes.c_size_t),
        ('MaximumWorkingSetSize', ctypes.c_size_t),
        ('ActiveProcessLimit', ctypes.c_uint32),
        ('Affinity', ctypes.c_size_t),
        ('PriorityClass', ctypes.c_uint32),
        ('SchedulingClass', ctypes.c_uint32),
    ]


class _JobObjectExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ('BasicLimitInformation', _JobObjectBasicLimitInformation),
        ('IoInfo', _IoCounters),
        ('ProcessMemoryLimit', ctypes.c_size_t),
        ('JobMemoryLimit', ctypes.c_size_t),
        ('PeakProcessMemoryUsed', ctypes.c_size_t),
        ('PeakJobMemoryUsed', ctypes.c_size_t),
    ]


def assign_windows_job(task_id: str, pid: int) -> None:
    """Put a freshly spawned agent process into this task's job.

    The spawn must be CREATE_SUSPENDED: assign BEFORE the first
    instruction runs, then resume, so no grandchild can be born
    outside the job (D-C3-4's race). No-op off Windows.
    """
    if not IS_WINDOWS:  # pragma: no cover - platform guard
        return
    try:  # pragma: no cover - Windows only, proved natively
        kernel = ctypes.windll.kernel32
        job = kernel.CreateJobObjectW(None, None)
        if not job:
            return
        info = _JobObjectExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = 0
        if not kernel.SetInformationJobObject(
            job,
            _JOB_OBJECT_ASSIGNED_PROCESS,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            kernel.CloseHandle(job)
            return
        proc = kernel.OpenProcess(_PROCESS_ALL_ACCESS, False, pid)
        if not proc:
            kernel.CloseHandle(job)
            return
        ok = kernel.AssignProcessToJobObject(job, proc)
        kernel.CloseHandle(proc)
        if not ok:
            kernel.CloseHandle(job)
            return
        psutil.Process(pid).resume()  # lift the CREATE_SUSPENDED
        _WIN_JOBS.setdefault(task_id, []).append(job)
        logger.info(
            'Task %s agent pid %d assigned to its job object',
            task_id[:8],
            pid,
        )
    except Exception:
        logger.warning(
            'Job-object assign failed for task %s pid %s',
            task_id[:8],
            pid,
            exc_info=True,
        )


def _terminate_windows_jobs(task_id: str) -> int:
    """Terminate (and drop) every job object this task's sessions
    hold. The members — the agent tree AND its detached children —
    die together; the handles close so nothing leaks."""
    if not IS_WINDOWS:  # pragma: no cover - platform guard
        return 0
    jobs = _WIN_JOBS.pop(task_id, [])
    if not jobs:  # pragma: no cover - Windows only
        return 0
    try:  # pragma: no cover - Windows only, proved natively
        kernel = ctypes.windll.kernel32
        terminated = 0
        for job in jobs:
            try:
                if kernel.TerminateJobObject(job, 1):
                    terminated += 1
            finally:
                kernel.CloseHandle(job)
        return terminated
    except Exception:
        logger.warning('TerminateJobObject failed for task %s', task_id[:8])
        return 0


# ── spawn-side helpers (called from the agent launch) ───────────────


def win_spawn_creationflags() -> int:
    """The Windows spawn flag that starts the agent FROZEN (0 on
    POSIX, so the spawn kwargs stay identical there); see
    :func:`assign_windows_job` for the assign-then-resume dance."""
    return getattr(subprocess, 'CREATE_SUSPENDED', 0)


async def arm_agent_job(task_id: str, pid: int) -> None:
    """Async wrapper around :func:`assign_windows_job` (a no-op off
    Windows) for the agent launch site."""
    if IS_WINDOWS:
        await asyncio.to_thread(assign_windows_job, task_id, pid)
