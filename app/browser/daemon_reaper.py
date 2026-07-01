"""Periodic reaper for orphaned browser-use daemon processes.

Runs every 5 minutes, compares running daemons against active
tasks in the DB.  Any daemon whose task ID is not in the active
set is killed.

Two identification methods (both backends now use ``--cdp-url``):
1. Full UUID from ``--cdp-url client-{UUID}`` (primary, used by both Chrome and Ziniao)
2. 8-char prefix from ``--session {slug}-{id[:8]}`` (legacy fallback, kept for backward compat)

Uses ``psutil`` for cross-platform process discovery and killing.
"""

import asyncio
from dataclasses import dataclass
import logging
import re

from sqlalchemy import select

from app.browser.process_utils import kill_with_escalation
from app.database import async_session
from app.models.task import Task
from app.platform import find_processes_by_pattern
from app.task_states import TaskStatus

logger = logging.getLogger(__name__)

_INTERVAL_SECONDS = 300  # 5 minutes

# Full UUID from --cdp-url ws://.../client-{UUID} (both Chrome and Ziniao)
_UUID_RE = re.compile(
    r'client-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}'
    r'-[0-9a-f]{4}-[0-9a-f]{12})'
)

# 8-char hex suffix from --session {slug}-{id[:8]} (legacy fallback).
# Anchored to end-of-arg to avoid matching store slugs that
# happen to end in hex characters.
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

    # Full UUID from --cdp-url (both Chrome and Ziniao), or None
    full_task_id: str | None = None
    # 8-char prefix from --session (legacy fallback), or None
    task_id_prefix: str | None = None


async def _get_daemon_pids() -> dict[int, DaemonInfo]:
    """Return {pid: DaemonInfo} for all daemons."""
    procs = await find_processes_by_pattern(
        'browser_use.skill_cli.daemon',
    )

    result: dict[int, DaemonInfo] = {}
    for pid, cmdline in procs.items():
        info = DaemonInfo()

        # Primary: full UUID from --cdp-url
        m = _UUID_RE.search(cmdline)
        if m:
            info.full_task_id = m.group(1)
        else:
            # Fallback: 8-char prefix from --session
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

    Returns the number of daemons killed.
    """
    daemons = await _get_daemon_pids()
    if not daemons:
        return 0

    active_ids = await _get_active_task_ids()
    # 8-char prefix set for Chrome daemon matching.
    # Collision chance ~1/4B per pair — accepted as
    # known limitation; reaper runs every 5 minutes so
    # leaked daemons from false negatives are short-lived.
    active_prefixes = {tid[:8] for tid in active_ids}

    orphans: list[tuple[int, str]] = []
    for pid, info in daemons.items():
        if info.full_task_id:
            if info.full_task_id not in active_ids:
                orphans.append((pid, info.full_task_id[:8]))
        elif info.task_id_prefix:
            if info.task_id_prefix not in active_prefixes:
                orphans.append((pid, info.task_id_prefix))
        # else: no identifiable task ID — skip (e.g. manual
        # sessions without VIBE_TASK_ID)

    # Terminate -> poll -> kill escalation.
    # browser-use daemons can hang indefinitely when the CDP
    # WebSocket is dead.
    # Run concurrently to avoid N * timeout serial blocking.
    await asyncio.gather(*(kill_with_escalation(pid) for pid, _ in orphans))

    if orphans:
        logger.info(
            'Reaped %d orphaned browser-use daemon(s): %s',
            len(orphans),
            [(pid, tid) for pid, tid in orphans],
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
