"""A module-level asyncio lock that works in every event loop.

An ``asyncio.Lock`` binds to the first event loop it is contended on
and raises ``RuntimeError: ... is bound to a different event loop`` if
it is ever contended in another. A lock created at import and kept in a
module therefore breaks as soon as a process runs more than one loop —
which a test process does, one per test: the second test that contends
it fails, whichever it is. ``LoopLock`` keeps one lock per running loop
(the server has exactly one), the same way ``app.workspace.skills_tree``
does for the skills tree.

Imports nothing from ``app``, so any module can use it.
"""

import asyncio


class LoopLock:
    """``async with`` lock, one ``asyncio.Lock`` per running event loop."""

    def __init__(self) -> None:
        self._lock: asyncio.Lock | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _for_running_loop(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._loop is not loop:
            self._lock = asyncio.Lock()
            self._loop = loop
        return self._lock

    async def __aenter__(self) -> None:
        await self._for_running_loop().acquire()

    async def __aexit__(self, *_exc) -> None:
        self._for_running_loop().release()
