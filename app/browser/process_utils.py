"""Shared process-management utilities for browser daemons."""

import asyncio

from app.platform import is_process_alive, kill_process

__all__ = [
    'is_process_alive',
    'kill_process',
    'kill_with_escalation',
    'taskkill_tree',
]


async def taskkill_tree(pid: int, timeout: float = 3.0) -> None:
    """Force-kill a process AND its whole child tree on Windows.

    ``os.killpg`` is POSIX-only; on Windows there is no process group to
    signal, and killing only the leader would orphan its children (MCP
    server, skill_cli.daemon, browser-use). ``taskkill /F /T`` walks the
    tree. Best-effort — never raises.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            'taskkill',
            '/F',
            '/T',
            '/PID',
            str(pid),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except Exception:
        pass


async def kill_with_escalation(
    pid: int,
    sigterm_timeout: float = 3.0,
    poll_interval: float = 0.1,
) -> bool:
    """Terminate a process, escalating to force-kill if needed.

    Delegates to :func:`app.platform.kill_process` which uses
    ``psutil`` for cross-platform process management.

    Returns ``True`` if the signal was sent (or the process was
    already dead).
    """
    return await kill_process(
        pid,
        timeout=sigterm_timeout,
        poll_interval=poll_interval,
    )
