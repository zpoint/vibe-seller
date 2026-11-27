"""Open and drive a macOS Electron app's main-process Node inspector.

The macOS sibling of the Windows trigger in :mod:`win_inspector`: a Mac
Electron app keeps the default Node behaviour where ``SIGUSR1`` makes
the main process open its inspector on 127.0.0.1:9229. Everything else
— the WebSocket protocol, ``eval_in_renderer`` (BrowserWindow.
executeJavaScript), the stale-target retry, closing via
``require('inspector').close()``, the machine-wide inspector lock, busy
refusal and pid verification — is the shared machinery in
:mod:`win_inspector`; the base ``open`` holds all of it for every
platform, so this module only swaps HOW the inspector is opened:
:meth:`MacInspectorSession._trigger` sends SIGUSR1 instead of
launching the exe under ``ELECTRON_RUN_AS_NODE``.

Validated live (2026-11-27): ``kill -USR1 <main pid>`` opens 9229 with
one ``node`` target; ``require('inspector').close()`` closes it.

This module is vendor-neutral: it knows Electron and macOS, not any
particular client.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal

from app.browser.win_inspector import InspectorError, InspectorSession

__all__ = ['MacInspectorSession', 'send_usr1']

logger = logging.getLogger(__name__)


def send_usr1(pid: int) -> None:
    """``SIGUSR1`` to ``pid`` (seam; the signal that opens the inspector)."""
    os.kill(pid, signal.SIGUSR1)


class MacInspectorSession(InspectorSession):
    """An :class:`~app.browser.win_inspector.InspectorSession` opened
    with SIGUSR1 instead of a Windows debug launch.

    The base class needs the PowerShell preamble only for the Windows
    trigger, so a Mac session passes a stub. Override ONLY ``_trigger``
    (see the base docstring): the base ``open`` holds the machine-wide
    inspector lock, refuses a busy port and verifies the pid for every
    platform.
    """

    def __init__(self, host: str = '127.0.0.1', port: int = 9229):
        super().__init__(lambda: '', host=host, port=port)

    async def _trigger(self, main_pid: int) -> None:
        """Make the target open its inspector: SIGUSR1, then return.

        Contract (base class): return once the trigger is ISSUED; never
        connect to the inspector ourselves. A stale pid (the client
        died between the caller reading ``main_pid`` and this signal)
        surfaces as ``InspectorError`` — the base ``open`` releases its
        lock and re-raises it, and the caller reads a clean
        client-not-running signal instead of an untyped OS error.
        """
        try:
            await asyncio.to_thread(send_usr1, main_pid)
        except ProcessLookupError as e:
            raise InspectorError(f'target process {main_pid} is gone') from e
