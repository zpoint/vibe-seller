"""Locate/kill browser-use 0.13 (browser_harness) daemons by ``BU_NAME``.

browser-use 0.13 spawns each daemon as ``python -m browser_harness.daemon``
with the session name in the ``BU_NAME`` env var (not argv) and records it
as ``<BH_RUNTIME_DIR>/bu-<BU_NAME>.pid`` (+ ``.sock``). Identity therefore
lives in the PID FILE, keyed on ``BU_NAME`` — the wrapper sets
``BU_NAME={slug}-{task_id[:8]}`` (or ``{slug}-aux`` / ``web-{id8}``).

These are the low-level primitives shared by the reaper
(:mod:`app.browser.daemon_reaper`), the per-task cleanup
(:class:`ClaudeCodeBackend`), and the aux/all-kill paths
(:mod:`app.browser.manager`). Killing is guarded against PID reuse by
cross-referencing each pid file against the set of live
``browser_harness.daemon`` processes. See
docs/browser-use-0.13-migration.md.
"""

from collections.abc import Callable, Iterator
import json
import logging
from pathlib import Path

from app.browser.process_utils import kill_with_escalation
from app.config import BH_RUNTIME_DIR
from app.platform import find_processes_by_pattern

logger = logging.getLogger(__name__)

# `python -m browser_harness.daemon` (0.13) and the legacy 0.12 daemon.
NEW_DAEMON_PATTERN = 'browser_harness.daemon'
LEGACY_DAEMON_PATTERN = 'browser_use.skill_cli.daemon'


def parse_pid_record(text: str) -> int | None:
    """PID from a ``bu-<BU_NAME>.pid`` record, or None if unparseable.

    Accepts both shapes browser_harness writes: a plain int, and the
    JSON ``{"pid": ..., "started": ...}`` record. Never guesses a pid:
    only a real ``int`` or a string of ASCII digits counts, so a
    ``bool``/``float`` payload can never parse as PID 0/1.
    """
    raw = text.strip()
    try:
        value = json.loads(raw)['pid']
    except (json.JSONDecodeError, TypeError, KeyError):
        try:
            value = raw.split()[0]
        except IndexError:
            return None
    # bool is an int subclass — exclude it, or ``{"pid": true}`` parses
    # as PID 1 (init).
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    return None


def iter_pidfiles() -> Iterator[tuple[str, int | None, Path, Path]]:
    """Yield ``(bu_name, pid|None, pidfile, sockfile)`` per ``bu-*.pid``.

    ``pid`` is None when the file is unreadable/corrupt. Callers decide
    whether a pid is live before acting on it.
    """
    runtime = BH_RUNTIME_DIR
    if not runtime.is_dir():
        return
    for pf in sorted(runtime.glob('bu-*.pid')):
        bu_name = pf.name[len('bu-') : -len('.pid')]
        sock = pf.with_suffix('.sock')
        try:
            pid = parse_pid_record(pf.read_text())
        except OSError:
            pid = None
        yield bu_name, pid, pf, sock


def unlink_quiet(*paths: Path) -> None:
    for p in paths:
        try:
            p.unlink()
        except (FileNotFoundError, OSError):
            pass


async def kill_bh_daemons(name_matches: Callable[[str], bool]) -> int:
    """Kill live 0.13 daemons whose ``BU_NAME`` satisfies ``name_matches``.

    A pid file is acted on only when its PID is a live
    ``browser_harness.daemon`` (guards PID reuse); the pid/sock files are
    cleaned up either way. Returns the number of daemons killed.
    """
    live = await find_processes_by_pattern(NEW_DAEMON_PATTERN)
    live_pids = set(live)
    killed = 0
    for bu_name, pid, pf, sock in list(iter_pidfiles()):
        if not name_matches(bu_name):
            continue
        if pid is not None and pid in live_pids:
            await kill_with_escalation(pid)
            killed += 1
            unlink_quiet(pf, sock)
        elif pid is None or pid not in live_pids:
            # Stale file (daemon gone / PID reused) — just clean up.
            unlink_quiet(pf, sock)
    return killed
