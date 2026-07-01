"""Cross-platform abstractions for Windows/macOS/Linux.

Centralises all platform-specific logic so the rest of the codebase
can import ``IS_WINDOWS``, path helpers, and process utilities from
a single module.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import sys

import psutil

logger = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == 'win32'
IS_MAC = sys.platform == 'darwin'
IS_LINUX = sys.platform.startswith('linux')


# -- venv path helpers ------------------------------------------------


def venv_bin_dir(venv: Path) -> Path:
    """Return the executables directory inside a venv."""
    return venv / ('Scripts' if IS_WINDOWS else 'bin')


def venv_python(venv: Path) -> Path:
    """Return the python interpreter path inside a venv."""
    if IS_WINDOWS:
        return venv / 'Scripts' / 'python.exe'
    return venv / 'bin' / 'python3'


def venv_executable(venv: Path, name: str) -> Path:
    """Return path to a named executable inside a venv.

    On Windows, appends ``.exe`` and maps common Unix names
    (``python3`` -> ``python``, ``pip3`` -> ``pip``).
    """
    if IS_WINDOWS:
        # Windows venvs use python.exe / pip.exe (no "3" suffix)
        win_name = {
            'python3': 'python',
            'pip3': 'pip',
        }.get(name, name)
        return venv / 'Scripts' / f'{win_name}.exe'
    return venv / 'bin' / name


def agent_venv_python() -> str:
    """The ``--python`` value for building the agent workspace venv.

    On a packaged install the installer/tray sets
    ``VIBE_SELLER_BUNDLED_PYTHON`` to the bundled interpreter — reuse
    it so ``uv venv`` does NOT download a second Python (we already
    shipped one). Everywhere else (curl / dev install) fall back to
    the version string ``'3.11'``, which uv resolves and fetches if
    absent — unchanged behaviour.
    """
    bundled = os.environ.get('VIBE_SELLER_BUNDLED_PYTHON', '')
    if bundled and Path(bundled).is_file():
        return bundled
    return '3.11'


# -- PATH helpers -----------------------------------------------------


def prepend_to_path(env: dict, *dirs: Path) -> None:
    """Prepend *dirs* to ``env['PATH']`` using the OS path separator."""
    current = env.get('PATH', '')
    prefix = os.pathsep.join(str(d) for d in dirs)
    env['PATH'] = f'{prefix}{os.pathsep}{current}' if current else prefix


# -- Process management (psutil-based, cross-platform) ----------------


def is_process_alive(pid: int) -> bool:
    """Check whether a process with *pid* exists."""
    return psutil.pid_exists(pid)


async def kill_process(
    pid: int,
    timeout: float = 3.0,
    poll_interval: float = 0.1,
) -> bool:
    """Terminate a process, escalating to force-kill if needed.

    Sends ``terminate()`` (SIGTERM on Unix, TerminateProcess on
    Windows), polls for *timeout* seconds, then ``kill()`` (SIGKILL
    on Unix, same TerminateProcess on Windows).

    Best-effort: returns ``True`` if the signal was sent (or the
    process was already dead), ``False`` if we lacked permission to
    signal it. Never raises on the expected psutil errors, so callers
    (CLI stop, daemon cleanup) don't abort on a stale/foreign PID.
    """
    try:
        proc = psutil.Process(pid)
        proc.terminate()
    except psutil.NoSuchProcess:
        return True
    except psutil.AccessDenied:
        logger.warning('No permission to terminate PID %d', pid)
        return False

    elapsed = 0.0
    while elapsed < timeout:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
        if not psutil.pid_exists(pid):
            return True

    # Still alive — escalate.
    try:
        proc = psutil.Process(pid)
        proc.kill()
        logger.debug(
            'Escalated PID %d to kill (survived %.1fs terminate)',
            pid,
            timeout,
        )
    except psutil.NoSuchProcess:
        pass
    except psutil.AccessDenied:
        logger.warning('No permission to kill PID %d', pid)
        return False
    return True


async def find_processes_by_pattern(
    pattern: str,
) -> dict[int, str]:
    """Return ``{pid: cmdline_string}`` for processes whose command
    line contains *pattern*.

    ``psutil.process_iter`` works on all platforms but is a blocking
    scan (slow with many processes / slow /proc access), so run it in
    a worker thread to avoid stalling the event loop.
    """

    def _scan() -> dict[int, str]:
        result: dict[int, str] = {}
        for proc in psutil.process_iter(['pid', 'cmdline']):
            try:
                cmdline = proc.info.get('cmdline') or []
                full = ' '.join(cmdline)
                if pattern in full:
                    result[proc.info['pid']] = full
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return result

    return await asyncio.to_thread(_scan)


# -- File permissions -------------------------------------------------


def safe_chmod(path: Path | str, mode: int) -> None:
    """Set file permissions on Unix; no-op on Windows.

    Windows NTFS does not use Unix permission bits.  Files under
    ``%USERPROFILE%`` are already user-scoped on typical single-user
    machines.
    """
    if not IS_WINDOWS:
        os.chmod(str(path), mode)
