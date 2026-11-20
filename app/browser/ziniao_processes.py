"""Find and stop every process that belongs to the Ziniao client.

Ziniao's WebDriver doc (``ziniao-open/skill-hub`` →
``skills/ziniao-webdriver-doc``, trap 3) is explicit: before launching the
client in WebDriver mode, and before any retry after a failed launch, stop
**every** Ziniao process — the main client and its kernel processes.
Leftovers keep the port and the profile directories, and the next launch
fails the same way. See docs/ziniao-concurrency.md § Client startup.

A process *name* match is not enough. On a Windows install with one store
open, the client runs ``ziniao.exe`` (×6), ``ziniao-gateway.exe`` and
``env-kit.exe`` from the install directory, plus ``ziniaobrowser.exe``
kernels and a ``node.exe`` under ``%APPDATA%\\ziniaobrowser\\env-kit``;
older kernels are ``superbrowser.exe`` under ``SuperBrowserCores``. So a
process is Ziniao's when its executable lives in a directory Ziniao owns,
or — for the main client in a custom install folder, or when the path
cannot be read — when its name says so.

Never a command-line match (``pkill -f ziniao``): that would hit this
server when it runs from a checkout named ``*ziniao*``, or an agent whose
arguments mention Ziniao. Never Ziniao's updater/installer either: killing
an update half-way can leave the install broken.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import time

import psutil

from app.platform import IS_WINDOWS

logger = logging.getLogger(__name__)

_MAIN = 'ziniao'  # the client itself: ziniao.exe / ziniao.app/…/ziniao
_NAME_PREFIX = 'ziniao'  # ziniaobrowser, ziniao-gateway, …
_NAMES_IF_NO_PATH = frozenset({'superbrowser'})  # older kernels
# Directory components only Ziniao uses.
_DIRS = frozenset({
    'ziniao',  # C:\Program Files\ziniao\…, /opt/ziniao/…
    'ziniao.app',  # macOS bundle (main + helpers)
    'ziniaobrowser',  # kernels + env-kit under the user profile
    'ziniaobrowser-kernel',
    'superbrowsercores',
})
# Never stop these: an interrupted update corrupts the install.
_NEVER_DIRS = frozenset({'ziniaobrowser-updater'})
_NEVER_WORDS = ('install', 'uninst', 'setup', 'update', '_production')

_KILL_WAIT_S = 10.0
_TERM_GRACE_S = 3.0
_WSL_POWERSHELL = (
    '/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe'
)
_WSL_TASKKILL = '/mnt/c/Windows/System32/taskkill.exe'
_WSL_CWD = '/mnt/c/'
_SINGLETONS = ('SingletonLock', 'SingletonSocket', 'SingletonCookie')


@dataclass(frozen=True)
class ZiniaoProcess:
    pid: int
    name: str
    exe: str | None
    user_data_dir: str | None = None


@dataclass
class KillReport:
    """What ``kill_all_ziniao`` stopped and what it could not."""

    killed: list[ZiniaoProcess] = field(default_factory=list)
    survivors: list[ZiniaoProcess] = field(default_factory=list)

    @property
    def main_was_running(self) -> bool:
        return any(is_main(p) for p in self.killed)


def _stem(name: str) -> str:
    name = (name or '').strip().lower()
    return name[:-4] if name.endswith('.exe') else name


def _norm(path: str) -> str:
    return path.replace('\\', '/').lower()


def _dir_parts(exe: str | None) -> list[str]:
    if not exe:
        return []
    return [p for p in _norm(exe).split('/') if p][:-1]


def is_main(p: ZiniaoProcess) -> bool:
    """The client process (vs a kernel or helper)."""
    return _stem(p.name) == _MAIN


def install_dirs_of(
    procs: list[ZiniaoProcess], client_path: str = ''
) -> set[str]:
    """Folders the running client was installed to.

    The main executable's folder covers a custom install location
    (``D:\\紫鸟\\ziniao.exe``) whose helpers (``env-kit.exe``) carry no
    Ziniao name. A configured full ``client_path`` counts too.
    """
    paths = [p.exe for p in procs if p.exe and is_main(p)]
    if client_path and re.search(r'[\\/]', client_path):
        paths.append(client_path)
    return {os.path.dirname(_norm(path)).rstrip('/') + '/' for path in paths}


def is_ziniao_process(
    name: str,
    exe: str | None,
    install_dirs: set[str] | frozenset = frozenset(),
) -> bool:
    """True when the process belongs to the Ziniao client (the one rule)."""
    stem = _stem(name)
    parts = _dir_parts(exe)
    if any(p in _NEVER_DIRS for p in parts):
        return False
    if any(w in stem for w in _NEVER_WORDS):
        return False
    if any(p in _DIRS for p in parts):
        return True
    norm = _norm(exe) if exe else ''
    if norm and any(norm.startswith(d) for d in install_dirs):
        return True
    if stem.startswith(_NAME_PREFIX):
        return True
    return not exe and stem in _NAMES_IF_NO_PATH


def _own_lineage() -> set[int]:
    """This process and its ancestors — never ours to kill. (Children are
    fair game: the client we launched is one.)"""
    pids = {os.getpid()}
    try:
        pids.update(p.pid for p in psutil.Process().parents())
    except (psutil.Error, OSError):
        pass
    return pids


def _user_data_dir(pid: int) -> str | None:
    try:
        for arg in psutil.Process(pid).cmdline():
            if arg.startswith('--user-data-dir='):
                return arg.split('=', 1)[1].strip('"')
    except (psutil.Error, OSError):
        pass
    return None


def _list_native() -> list[ZiniaoProcess]:
    return [
        ZiniaoProcess(
            pid=proc.info['pid'],
            name=proc.info.get('name') or '',
            exe=proc.info.get('exe'),
        )
        for proc in psutil.process_iter(['pid', 'name', 'exe'])
    ]


def _powershell(script: str, timeout: float = 60) -> str:
    result = subprocess.run(
        [
            _WSL_POWERSHELL,
            '-NoProfile',
            '-NonInteractive',
            '-Command',
            '[Console]::OutputEncoding=[Text.Encoding]::UTF8;' + script,
        ],
        capture_output=True,
        timeout=timeout,
        cwd=_WSL_CWD,
    )
    if result.returncode != 0:
        err = result.stderr.decode('utf-8', errors='replace')[:300]
        raise RuntimeError(
            f'powershell.exe failed (rc={result.returncode}): {err}'
        )
    return result.stdout.decode('utf-8', errors='replace').strip()


def _list_wsl() -> list[ZiniaoProcess]:
    """Windows processes seen from WSL via interop. Pre-filtered in
    PowerShell to keep the output small; the Python rule decides."""
    raw = _powershell(
        'Get-CimInstance Win32_Process | Where-Object { '
        "$_.Name -match 'ziniao|superbrowser' -or "
        "$_.ExecutablePath -match 'ziniao|superbrowser' } | "
        'Select-Object ProcessId,Name,ExecutablePath | '
        'ConvertTo-Json -Compress'
    )
    if not raw:
        return []
    rows = json.loads(raw)
    if isinstance(rows, dict):
        rows = [rows]
    return [
        ZiniaoProcess(
            pid=int(r['ProcessId']),
            name=r.get('Name') or '',
            exe=r.get('ExecutablePath'),
        )
        for r in rows
        if r.get('ProcessId')
    ]


def wsl_port_has_listener(port: int) -> bool:
    """Does anything LISTEN on ``port`` on the Windows side?

    Raises when interop does not work — the caller must then not kill:
    without this answer it cannot tell normal mode from a healthy client
    that WSL simply cannot reach.
    """
    out = _powershell(
        f'@(Get-NetTCPConnection -State Listen -LocalPort {int(port)} '
        '-ErrorAction SilentlyContinue).Count',
        timeout=30,
    )
    return int(out or '0') > 0


def list_ziniao_processes(
    wsl: bool, client_path: str = ''
) -> list[ZiniaoProcess]:
    """Every running Ziniao process (blocking — call via ``to_thread``)."""
    procs = _list_wsl() if wsl else _list_native()
    dirs = install_dirs_of(procs, client_path)
    own = set() if wsl else _own_lineage()
    found = [
        p
        for p in procs
        if p.pid not in own and is_ziniao_process(p.name, p.exe, dirs)
    ]
    if wsl or IS_WINDOWS:
        return found
    return [
        ZiniaoProcess(p.pid, p.name, p.exe, _user_data_dir(p.pid))
        for p in found
    ]


def _stop(pid: int, hard: bool) -> None:
    try:
        proc = psutil.Process(pid)
        if hard:
            proc.kill()  # SIGKILL / TerminateProcess
        else:
            proc.terminate()  # SIGTERM
    except psutil.NoSuchProcess:
        pass
    except psutil.Error as e:
        logger.warning('Could not stop pid %d: %s', pid, e)


def _kill(wsl: bool, procs: list[ZiniaoProcess]) -> None:
    if not procs:
        return
    if wsl:
        args = [_WSL_TASKKILL, '/F']
        for p in procs:
            args += ['/PID', str(p.pid)]
        # Non-zero when one is already gone; the re-list decides.
        subprocess.run(args, capture_output=True, timeout=30, cwd=_WSL_CWD)
        return
    if IS_WINDOWS:
        for p in procs:
            _stop(p.pid, hard=True)
        return
    # POSIX: the client respawns kernels and survives SIGTERM, so it goes
    # first and hard. Kernels get SIGTERM and a short grace so Chrome
    # removes its own Singleton* files; SIGKILL whatever is left.
    rest = []
    for p in procs:
        if is_main(p):
            _stop(p.pid, hard=True)
            continue
        _stop(p.pid, hard=False)
        try:
            rest.append(psutil.Process(p.pid))
        except psutil.Error:
            pass
    _, still = psutil.wait_procs(rest, timeout=_TERM_GRACE_S)
    for proc in still:
        _stop(proc.pid, hard=True)


def clear_singleton_locks(userdata_dir: str | None) -> None:
    """Remove stale Chrome ``Singleton*`` files from a user-data dir.

    A crash or SIGKILL leaves them behind; the next Chrome launch then
    comes up without binding its remote-debugging port (the "stale
    launch"). Safe only when no Chrome uses the dir. Best-effort: never
    raises. See docs/ziniao-concurrency.md.
    """
    if not userdata_dir:
        return
    try:
        base = Path(userdata_dir)
        for name in _SINGLETONS:
            f = base / name
            if f.exists() or f.is_symlink():
                f.unlink(missing_ok=True)
                logger.info('Cleared stale Chrome %s in %s', name, base.name)
    except Exception as e:
        logger.debug('Singleton-lock cleanup skipped (%s)', e)


def kill_all_ziniao(wsl: bool, client_path: str = '') -> KillReport:
    """Stop every Ziniao process and wait until none is left.

    Blocking — call via ``asyncio.to_thread``. Re-lists after each kill
    (the client can respawn a helper while it dies) for up to 10 s;
    whatever is still alive then is a survivor — typically a client
    started as administrator that this user cannot stop.
    """
    report = KillReport()
    seen: dict[int, ZiniaoProcess] = {}
    deadline = time.monotonic() + _KILL_WAIT_S
    while True:
        alive = list_ziniao_processes(wsl, client_path)
        for p in alive:
            seen.setdefault(p.pid, p)
        if not alive:
            break
        if time.monotonic() >= deadline:
            report.survivors = alive
            break
        _kill(wsl, alive)
        time.sleep(0.5)
    left = {p.pid for p in report.survivors}
    report.killed = [p for pid, p in seen.items() if pid not in left]
    if not report.survivors:
        for d in {p.user_data_dir for p in report.killed if p.user_data_dir}:
            clear_singleton_locks(d)
    return report


def describe(procs: list[ZiniaoProcess]) -> str:
    """``ziniao.exe ×6, env-kit.exe`` — for logs and messages."""
    counts: dict[str, int] = {}
    for p in procs:
        counts[p.name] = counts.get(p.name, 0) + 1
    return ', '.join(
        f'{n} ×{c}' if c > 1 else n for n, c in sorted(counts.items())
    )
