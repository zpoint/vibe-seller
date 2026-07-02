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


# Signature of a vibe-seller headless task agent (`claude -p` with the
# bidirectional stream-json protocol) — a combo interactive Claude Code
# never uses, so matching it can't hit an unrelated Claude session. Plus
# the browser daemon it spawns.
_AGENT_CMDLINE_PATTERN = 'output-format stream-json --input-format stream-json'
_BROWSER_DAEMON_PATTERN = 'skill_cli.daemon'
# The per-store wrapper. An agent that died ungracefully (a bare -9
# instead of a process-group kill) can orphan a `bash .../browser-use
# eval …` poll-loop — reparented to init/launchd — that keeps hammering
# browser/start. Match the wrapper path AND a browser-use subcommand, so
# a *running* wrapper is reaped but a mere reference to the path (e.g. an
# editor viewing `~/.vibe-seller/bin/<slug>/browser-use`) is not.
_WRAPPER_PATH = '.vibe-seller/bin/'
_WRAPPER_VERBS = (
    'eval',
    'open',
    'state',
    'click',
    'close',
    'screenshot',
    'type',
    'input',
    'keys',
    'sessions',
    'get',
    'extract',
    'scroll',
    'wait',
    'hover',
    'dblclick',
    'rightclick',
    'select',
    'upload',
    'back',
)


def _is_task_process(cmdline: str) -> bool:
    """True if the command line is a vibe-seller task agent, its browser
    daemon, or a running per-store browser-use wrapper (not an editor
    merely referencing the wrapper path)."""
    if _AGENT_CMDLINE_PATTERN in cmdline or _BROWSER_DAEMON_PATTERN in cmdline:
        return True
    if _WRAPPER_PATH in cmdline and '/browser-use' in cmdline:
        return any(f'browser-use {v}' in cmdline for v in _WRAPPER_VERBS)
    return False


def collect_agent_descendants(pid: int) -> set[int]:
    """PIDs of task-agent processes descended from ``pid`` (a server).

    Used by ``vibe-seller stop`` to scope reaping to the stopped server's
    own agents. Must be called BEFORE the server is killed — once it
    exits, its agents reparent to init/launchd and are no longer its
    descendants. Best-effort; returns an empty set on any error.
    """
    try:
        kids = psutil.Process(pid).children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return set()
    out: set[int] = set()
    for child in kids:
        try:
            if _is_task_process(' '.join(child.cmdline() or [])):
                out.add(child.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return out


async def reap_task_agents(pids: set[int] | None = None) -> int:
    """Kill orphaned task-agent process trees. Cross-platform.

    A stopped/restarted server can leave `claude -p` agents (and their
    MCP server / skill_cli.daemon / browser-use children) alive. An
    orphan keeps calling ``browser/start`` on the next server and
    thrashes the shared Ziniao client. The graceful in-process path
    (``agent_manager.stop_all``) only runs on a clean SIGTERM shutdown —
    Windows ``TerminateProcess`` (what ``vibe-seller stop`` / the tray
    quit-restart use) bypasses it — so the stop command calls this as a
    process-level backstop that works on every OS.

    ``pids``: when given, only these root PIDs are considered (used by
    ``vibe-seller stop`` to scope the reap to the *stopped server's own*
    agent subtree — collected before the server is killed — so a second
    server instance's agents are never touched). When ``None``, scans all
    processes for the task signatures.

    Kills each matched process together with its whole child tree via
    psutil (``children(recursive=True)``), which needs no ``killpg`` /
    ``taskkill`` and behaves identically on Windows and Unix. Best-effort:
    never raises; returns the number of root processes reaped.
    """
    own = os.getpid()

    def _reap() -> int:
        roots: dict[int, psutil.Process] = {}
        if pids is not None:
            for pid in pids:
                if pid == own:
                    continue
                try:
                    roots[pid] = psutil.Process(pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        else:
            for proc in psutil.process_iter(['pid', 'cmdline']):
                try:
                    if proc.info['pid'] == own:
                        continue
                    full = ' '.join(proc.info.get('cmdline') or [])
                    if _is_task_process(full):
                        roots[proc.info['pid']] = proc
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        if not roots:
            return 0
        # Gather each root + its descendants, then terminate → kill.
        victims: dict[int, psutil.Process] = {}
        for root in roots.values():
            victims[root.pid] = root
            try:
                for child in root.children(recursive=True):
                    victims[child.pid] = child
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        for p in victims.values():
            try:
                p.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        _gone, alive = psutil.wait_procs(list(victims.values()), timeout=3)
        for p in alive:
            try:
                p.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return len(roots)

    reaped = await asyncio.to_thread(_reap)
    if reaped:
        logger.info('Reaped %d orphaned task agent(s)/daemon(s)', reaped)
    return reaped


# -- File permissions -------------------------------------------------


def safe_chmod(path: Path | str, mode: int) -> None:
    """Set file permissions on Unix; no-op on Windows.

    Windows NTFS does not use Unix permission bits.  Files under
    ``%USERPROFILE%`` are already user-scoped on typical single-user
    machines.
    """
    if not IS_WINDOWS:
        os.chmod(str(path), mode)
