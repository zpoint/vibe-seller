"""Shared process-management utilities for browser daemons."""

from app.platform import is_process_alive, kill_process

__all__ = ['is_process_alive', 'kill_process', 'kill_with_escalation']


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
