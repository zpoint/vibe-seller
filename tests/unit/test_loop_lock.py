"""A module-level lock must work in every event loop the process runs.

A plain ``asyncio.Lock`` binds to the first loop it is contended on; a
second loop that contends it raises ``RuntimeError: ... is bound to a
different event loop``. That is what failed whichever store-create test
ran second on a CI worker. See app/loop_lock.py.
"""

import asyncio

import pytest

from app.loop_lock import LoopLock

pytestmark = pytest.mark.unit


async def _contend(lock) -> list[str]:
    order: list[str] = []

    async def worker(name: str) -> None:
        async with lock:
            order.append(f'{name} in')
            await asyncio.sleep(0.01)
            order.append(f'{name} out')

    await asyncio.gather(worker('a'), worker('b'))
    return order


def test_one_lock_serves_two_event_loops():
    lock = LoopLock()
    for _ in range(2):  # two loops, as two tests get
        order = asyncio.run(_contend(lock))
        # Still a lock: the second holder waits for the first.
        assert order == ['a in', 'a out', 'b in', 'b out']
