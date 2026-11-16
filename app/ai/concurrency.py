"""A concurrency limit that can be resized while callers wait on it.

``asyncio.Semaphore`` cannot change its size, and replacing it with a
new one strands every coroutine already blocked on the old object: the
releases go to the new semaphore and the old waiters are never woken.
Observed live: raising Settings → max agent concurrency from 2 to 5
left two queued fan-out tasks ``queued`` forever.

``ResizableLimiter`` is one object for the life of the process. The
limit and the in-flight count are its only state, so a resize is a
field write followed by a wake-up pass:

- raising the limit admits waiters at once, up to the new limit;
- lowering it stops nothing that already runs; new acquisitions wait
  until in-flight drops below the new limit;
- a slot is counted when it is granted and uncounted on release, so
  no resize can leak or double-count one.
"""

import asyncio
import collections


class ResizableLimiter:
    """FIFO counting limiter whose limit can change at runtime."""

    def __init__(self, limit: int):
        self._limit = max(1, int(limit))
        self._in_flight = 0
        self._waiters: collections.deque[asyncio.Future] = collections.deque()

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def in_flight(self) -> int:
        return self._in_flight

    @property
    def waiting(self) -> int:
        return sum(1 for f in self._waiters if not f.done())

    async def acquire(self) -> None:
        """Wait for a slot (first come, first served) and take it."""
        if not self._waiters and self._in_flight < self._limit:
            self._in_flight += 1
            return
        fut = asyncio.get_running_loop().create_future()
        self._waiters.append(fut)
        try:
            await fut
        except asyncio.CancelledError:
            # Granted, but cancelled before resuming: the slot was
            # counted for us, so hand it on.
            if fut.done() and not fut.cancelled():
                self.release()
            raise
        finally:
            try:
                self._waiters.remove(fut)
            except ValueError:
                pass

    def release(self) -> None:
        """Give back a slot taken by ``acquire``."""
        if self._in_flight <= 0:
            raise RuntimeError('ResizableLimiter released too many times')
        self._in_flight -= 1
        self._wake()

    def resize(self, limit: int) -> None:
        """Change the limit in place; admit waiters if it grew."""
        self._limit = max(1, int(limit))
        self._wake()

    def _wake(self) -> None:
        # The slot is counted at grant time, so a second wake pass (or
        # a resize racing a release) can never admit past the limit.
        while self._waiters and self._in_flight < self._limit:
            fut = self._waiters.popleft()
            if not fut.done():
                self._in_flight += 1
                fut.set_result(None)
