"""Periodic reaper for orphaned browser-use daemon processes.

Runs every 5 minutes (and once on boot), compares running daemons
against active tasks in the DB. Any daemon whose task ID is not in the
active set is killed.

browser-use 0.13 (browser_harness) spawns each daemon as
``python -m browser_harness.daemon`` with the session name in the
``BU_NAME`` *env var* (not argv) and records it as
``<BH_RUNTIME_DIR>/bu-<BU_NAME>.pid`` (+ ``.sock``). So identity comes
from the PID FILE, keyed on ``BU_NAME = {slug}-{task_id[:8]}`` — no
cmdline/env scraping (portable; avoids macOS ``psutil.environ()``
AccessDenied). We cross-reference each pid file against the set of live
``browser_harness.daemon`` processes to guard against PID reuse, and
delete stale files whose daemon is gone.

Legacy (browser-use 0.12) daemons — ``browser_use.skill_cli.daemon``
with ``--cdp-url``/``--session`` in argv — are still recognised for one
upgrade cycle so the first boot after an in-place upgrade reaps the
pre-upgrade daemons instead of orphaning them.

Uses ``psutil`` for cross-platform process discovery and killing.
"""

import asyncio
from dataclasses import dataclass, field
import logging
from pathlib import Path
import re

from sqlalchemy import select

from app.browser.bh_daemons import (
    LEGACY_DAEMON_PATTERN,
    NEW_DAEMON_PATTERN,
    iter_pidfiles,
    unlink_quiet,
)
from app.browser.process_utils import kill_with_escalation
from app.database import async_session
from app.models.task import Task
from app.platform import find_processes_by_pattern
from app.task_states import TaskStatus

logger = logging.getLogger(__name__)

_INTERVAL_SECONDS = 300  # 5 minutes

# BU_NAME (pid-file stem after `bu-`) ends in `-{task_id[:8]}` for a
# task daemon. Bare `{slug}` / `{slug}-aux` have no task suffix → skipped
# (manual / aux sessions, like the old no-VIBE_TASK_ID case).
_PIDFILE_TASK_RE = re.compile(r'-([0-9a-f]{8})$')

# Legacy 0.12 identifiers, from the daemon's argv.
_UUID_RE = re.compile(
    r'client-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}'
    r'-[0-9a-f]{4}-[0-9a-f]{12})'
)
_SESSION_ID_RE = re.compile(r'--session\s+\S+?-([0-9a-f]{8})(?:\s|$)')

# Statuses that indicate a task is still active
_ACTIVE_STATUSES = {
    TaskStatus.PENDING,
    TaskStatus.QUEUED,
    TaskStatus.DESIGNING,
    TaskStatus.PLANNED,
    TaskStatus.RUNNING,
    TaskStatus.WAITING,
}


@dataclass
class DaemonInfo:
    """Identification info for a browser-use daemon process."""

    # Full UUID from a legacy --cdp-url (0.12), or None
    full_task_id: str | None = None
    # 8-char task-id prefix (0.13 pid-file BU_NAME, or legacy --session)
    task_id_prefix: str | None = None
    # 0.13 daemon runtime files to clean up after a kill (pid + sock)
    cleanup_paths: list[Path] = field(default_factory=list)


async def _get_new_daemon_pids() -> dict[int, DaemonInfo]:
    """Map live 0.13 daemon PIDs → DaemonInfo via pid files.

    A pid file whose PID is not a live ``browser_harness.daemon``
    process is stale (crash / PID reuse); we delete it and skip it.
    """
    live = await find_processes_by_pattern(NEW_DAEMON_PATTERN)
    live_pids = set(live)

    result: dict[int, DaemonInfo] = {}
    for bu_name, pid, pf, sock in iter_pidfiles():
        if pid is None or pid not in live_pids:
            # Unreadable, daemon gone, or PID reused — stale file.
            unlink_quiet(pf, sock)
            continue
        m = _PIDFILE_TASK_RE.search(bu_name)
        result[pid] = DaemonInfo(
            task_id_prefix=m.group(1) if m else None,
            cleanup_paths=[pf, sock],
        )
    return result


async def _get_legacy_daemon_pids() -> dict[int, DaemonInfo]:
    """Map live 0.12 daemon PIDs → DaemonInfo via argv (upgrade compat)."""
    procs = await find_processes_by_pattern(LEGACY_DAEMON_PATTERN)
    result: dict[int, DaemonInfo] = {}
    for pid, cmdline in procs.items():
        info = DaemonInfo()
        m = _UUID_RE.search(cmdline)
        if m:
            info.full_task_id = m.group(1)
        else:
            m2 = _SESSION_ID_RE.search(cmdline)
            if m2:
                info.task_id_prefix = m2.group(1)
        result[pid] = info
    return result


async def _get_active_task_ids() -> set[str]:
    """Query DB for task IDs in active statuses."""
    async with async_session() as db:
        result = await db.execute(
            select(Task.id).where(
                Task.status.in_([s.value for s in _ACTIVE_STATUSES])
            )
        )
        return {str(row[0]) for row in result.all()}


async def reap_orphaned_daemons() -> int:
    """Kill daemons whose task ID is not in active tasks.

    Reaps both 0.13 (pid-file) and legacy 0.12 (argv) daemons. Returns
    the number of daemons killed.
    """
    new_daemons = await _get_new_daemon_pids()
    legacy_daemons = await _get_legacy_daemon_pids()
    # New wins on a PID collision (a live browser_harness.daemon can't
    # also be a skill_cli.daemon, but merge defensively).
    daemons = {**legacy_daemons, **new_daemons}
    if not daemons:
        return 0

    active_ids = await _get_active_task_ids()
    # 8-char prefix set. Collision chance ~1/4B per pair — accepted as a
    # known limitation; the reaper runs every 5 min so a false negative
    # is short-lived.
    active_prefixes = {tid[:8] for tid in active_ids}

    orphans: list[tuple[int, str, list[Path]]] = []
    for pid, info in daemons.items():
        if info.full_task_id:
            if info.full_task_id not in active_ids:
                orphans.append((pid, info.full_task_id[:8], info.cleanup_paths))
        elif info.task_id_prefix:
            if info.task_id_prefix not in active_prefixes:
                orphans.append((pid, info.task_id_prefix, info.cleanup_paths))
        # else: no identifiable task ID — skip (manual/aux sessions).

    # Terminate -> poll -> kill escalation. Daemons can hang indefinitely
    # when the CDP WebSocket is dead. Run concurrently to avoid
    # N * timeout serial blocking.
    await asyncio.gather(*(kill_with_escalation(pid) for pid, _, _ in orphans))
    # Remove the reaped 0.13 daemons' runtime files (legacy has none).
    for _pid, _tid, cleanup in orphans:
        if cleanup:
            unlink_quiet(*cleanup)

    if orphans:
        logger.info(
            'Reaped %d orphaned browser-use daemon(s): %s',
            len(orphans),
            [(pid, tid) for pid, tid, _ in orphans],
        )
    return len(orphans)


async def start_reaper_loop():
    """Run the reaper in a background loop forever."""
    while True:
        try:
            await asyncio.sleep(_INTERVAL_SECONDS)
            await reap_orphaned_daemons()
        except asyncio.CancelledError:
            break
        except Exception:
            logger.warning('Daemon reaper error', exc_info=True)
