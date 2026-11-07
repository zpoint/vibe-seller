"""One lock for the shared skills tree (``.claude/skills``).

Every MUTATION of the tree — the local sync mirror, the remote apply,
the gws bundle, the ads bundle, user skill create/save/delete — and
the copy of the tree into a task workspace run under this lock. The
task-workspace copy is the one READER that locks: a copy that catches a
swap mid-flight gets a partial skill dir, and the agent loads it that
way. Readers that merely list the tree skip vanished paths instead of
locking (see :mod:`app.workspace.fs_walk`).

The lock is an ``asyncio.Lock`` held across ``await
asyncio.to_thread(...)``. That is correct: the holder is a coroutine
already running on the loop, so nothing blocks the loop waiting for
it, and the thread's work is ordered by the await. An
``asyncio.Lock`` binds to the first event loop it is contended on, so
the lock is kept per running loop — the server has exactly one, and a
test process that runs several gets one per loop.

Import direction is safe by construction: this module imports nothing
from ``app``, so every writer can import it.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

_lock: asyncio.Lock | None = None
_lock_loop: asyncio.AbstractEventLoop | None = None


def _for_running_loop() -> asyncio.Lock:
    """The skills-tree lock for the running event loop."""
    global _lock, _lock_loop
    loop = asyncio.get_running_loop()
    if _lock is None or _lock_loop is not loop:
        _lock = asyncio.Lock()
        _lock_loop = loop
    return _lock


@asynccontextmanager
async def hold() -> AsyncIterator[None]:
    """Hold the skills tree while mutating or copying it."""
    async with _for_running_loop():
        yield
