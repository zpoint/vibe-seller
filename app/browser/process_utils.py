"""Shared process-management utilities for browser daemons."""

import asyncio
import logging
import os
import signal

logger = logging.getLogger(__name__)


async def kill_with_escalation(
    pid: int,
    sigterm_timeout: float = 3.0,
    poll_interval: float = 0.1,
) -> bool:
    """Send SIGTERM, poll, then SIGKILL if the process survives.

    browser-use daemons trap SIGTERM and attempt a graceful shutdown
    that can hang indefinitely when the CDP WebSocket is dead.  This
    helper attempts to ensure the process exits within
    *sigterm_timeout* seconds by sending SIGTERM and, if needed,
    SIGKILL, but it does not guarantee that the process is gone when
    it returns.

    Returns ``True`` if SIGTERM or SIGKILL was successfully sent (or
    the process was already dead).  It does not verify that the
    process has actually exited after escalation.
    """
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True

    elapsed = 0.0
    while elapsed < sigterm_timeout:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True  # died from SIGTERM

    # Still alive — escalate.
    try:
        os.kill(pid, signal.SIGKILL)
        logger.debug(
            'Escalated PID %d to SIGKILL (survived %.1fs SIGTERM)',
            pid,
            sigterm_timeout,
        )
    except ProcessLookupError:
        pass
    return True
