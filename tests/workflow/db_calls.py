"""Database calls in flight on the workflow test engine.

The workflow engine is one SQLite file behind ``StaticPool``: every
session shares a single connection. Cancelling a coroutine while it
is inside a database call makes SQLAlchemy invalidate that connection
(``_exec_single_context`` catches ``BaseException``) and open a second
one while the aiosqlite worker may still be finishing the first call's
write — the next writer on the new connection then fails with
``database is locked``. Anything in the test harness that cancels a
coroutine must therefore do it while no call is in flight.
``_TrackedAsyncSession`` (conftest) keeps the count; ``wait_db_idle``
is the one place that waits on it.
"""

import asyncio
import time

_real_sleep = asyncio.sleep


class DbCallTracker:
    """Counts awaitable session calls currently suspended.

    Incremented before the call suspends, decremented after it
    resumes, on the event-loop thread — so ``in_flight == 0`` means
    every task is parked at a non-database await, which is exactly
    when a ``cancel()`` cannot land inside a call.
    """

    def __init__(self) -> None:
        self.in_flight = 0


db_calls = DbCallTracker()


async def wait_db_idle(deadline_seconds: float = 10.0) -> None:
    """Return once no database call is in flight.

    The caller must cancel with no ``await`` between this returning
    and the ``cancel()``: on a single-threaded loop nothing else runs
    in between, so the idle state still holds at the cancel.
    ``deadline_seconds`` is a backstop for a hung worker only.
    """
    deadline = time.monotonic() + deadline_seconds
    while db_calls.in_flight and time.monotonic() < deadline:
        await _real_sleep(0.002)
