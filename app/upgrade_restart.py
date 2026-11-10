"""The server restarts itself onto an upgraded install.

An upgrade (``vibe-seller upgrade`` / ``uv tool upgrade`` / ``pip install
-U``) replaces the files on disk; the running process keeps the old code
in memory — and its lazy imports would start mixing in new modules. The
invariant: **a server never keeps serving code the disk no longer has.**

The server owns that invariant, not the command that ran the upgrade,
so it holds however the upgrade was done and however the server is run
(``vibe-seller start``, ``--foreground``, launchd/systemd, ``start.sh``):

1. ``watch()`` compares the version this process loaded
   (``app.version.get_version``, fixed per process) with the version now
   installed on disk.
2. When they differ it drains: the task queue stops dispatching, so the
   live agents can only go down. Queued tasks stay QUEUED and are
   re-queued on the next boot.
3. When no agent is live — the same set a shutdown would kill
   (``agent_manager.live_agent_count``) — it asks uvicorn for a graceful
   shutdown, and the lifespan's last step re-execs the same command
   line: same PID (pid files and supervisors keep tracking it), same
   environment, same host/port, new code.

Windows is excluded: there, upgrades go through the installer, which
stops the server and relaunches it itself (``installer/windows``).
"""

import asyncio
from importlib.metadata import PackageNotFoundError, version as _pkg_version
import logging
import multiprocessing
import os
import signal
import sys
import time

from app import telemetry
from app.telemetry_events import TelemetryEvent
from app.version import get_version

logger = logging.getLogger(__name__)

#: How often the server looks at the installed version. One metadata
#: read per interval; a restart needs to land within a minute of the
#: upgrade, not within a second.
CHECK_INTERVAL_SECONDS = 10.0

_pending_version: str | None = None
_restart_requested = False
_drain_started_at: float | None = None


def installed_version() -> str | None:
    """The version on disk now (NOT cached — that is the point)."""
    try:
        return _pkg_version('vibe-seller')
    except PackageNotFoundError:
        return None


def pending_version() -> str | None:
    """The upgraded version this server is draining to restart onto."""
    return _pending_version


def supported() -> bool:
    """Whether this process can restart itself in place.

    Not on Windows (no in-place exec; the installer owns restarts), and
    not in a uvicorn ``--reload`` worker (its parent reloader owns it).
    """
    if sys.platform == 'win32':
        return False
    return multiprocessing.parent_process() is None


async def check_once(task_queue, agents) -> bool:
    """One staleness check. True once a restart has been requested."""
    global _pending_version, _drain_started_at
    if _restart_requested:
        return True
    on_disk = await asyncio.to_thread(installed_version)
    loaded = get_version()
    if on_disk is None or on_disk == loaded:
        return False
    if _pending_version != on_disk:
        logger.warning(
            'vibe-seller %s is installed but %s is running — restarting '
            'onto it once no task agent is running',
            on_disk,
            loaded,
        )
        _pending_version = on_disk
        _drain_started_at = time.monotonic()
    task_queue.pause_dispatch()
    # No await between this check and the shutdown request: on one event
    # loop nothing can start an agent in between.
    live = agents.live_agent_count()
    if live:
        logger.info('Upgrade restart waiting on %d live agent(s)', live)
        return False
    request_restart()
    return True


async def watch(task_queue, agents) -> None:
    """Background loop started by the app lifespan."""
    if not supported():
        return
    while True:
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
        try:
            if await check_once(task_queue, agents):
                return
        except Exception:
            logger.exception('upgrade restart check failed')


def request_restart() -> None:
    """Graceful shutdown now; ``reexec_if_requested`` finishes the job."""
    global _restart_requested
    _restart_requested = True
    logger.warning('Restarting onto vibe-seller %s', _pending_version)
    _send_restarted()
    # uvicorn's own SIGTERM handler: stop accepting, finish requests, run
    # the lifespan shutdown (agents, schedulers, sync) — then re-exec.
    os.kill(os.getpid(), signal.SIGTERM)


def _send_restarted() -> None:
    waited = (
        None
        if _drain_started_at is None
        else time.monotonic() - _drain_started_at
    )
    try:
        telemetry.send(
            TelemetryEvent.UPGRADE_RESTARTED,
            {
                'from_version': get_version(),
                'to_version': _pending_version,
                'drain_wait_bucket': telemetry.duration_bucket(waited),
            },
        )
    except Exception:
        logger.debug('upgrade_restarted telemetry failed', exc_info=True)


def restart_argv() -> list[str]:
    """The exact command line this process was started with."""
    return [sys.executable, *sys.orig_argv[1:]]


def reexec_if_requested() -> None:
    """Last step of the lifespan shutdown: become the new version.

    ``execv`` keeps the PID, environment, cwd and the inherited stdout/
    stderr (the daemon log); every other fd is close-on-exec, so the
    listening socket is already released.
    """
    if not _restart_requested:
        return
    argv = restart_argv()
    logger.warning('Re-executing: %s', ' '.join(argv))
    logging.shutdown()
    os.execv(argv[0], argv)
