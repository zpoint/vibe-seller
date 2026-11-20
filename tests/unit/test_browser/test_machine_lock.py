"""The machine-wide lock is owned by the OS: never stuck after a restart.

The user's requirement for the Ziniao startup lock: it must stop two
processes restarting Ziniao at once, and "重启后要能用，不能无限锁住" — after
a restart (or a crash, or kill -9) nothing may stay locked. An OS-owned
lock (flock / msvcrt.locking) is dropped with its process; a lock FILE
left on disk means nothing.
"""

import asyncio
import os
import sys
import textwrap

import pytest

from app.browser.machine_lock import MachineLock, MachineLockTimeout

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)


async def test_a_second_holder_waits_then_times_out_naming_the_first(tmp_path):
    first = MachineLock('t', tmp_path)
    assert await first.acquire(timeout=1) is False
    second = MachineLock('t', tmp_path)
    with pytest.raises(MachineLockTimeout) as e:
        await second.acquire(timeout=0.3)
    assert e.value.holder == str(os.getpid())
    # Released while a waiter polls: the waiter gets it and knows it waited.
    waiter = asyncio.ensure_future(second.acquire(timeout=5))
    await asyncio.sleep(0.4)
    assert not waiter.done()
    first.release()
    assert await waiter is True
    second.release()


async def test_a_holder_that_is_killed_releases_the_lock(tmp_path):
    """kill -9 / a crashed or restarted server: the file stays, the lock
    does not — the next process gets it at once."""
    script = textwrap.dedent(
        f"""
        import sys, time, asyncio
        sys.path.insert(0, {ROOT!r})
        from pathlib import Path
        from app.browser.machine_lock import MachineLock
        lock = MachineLock('t', Path({str(tmp_path)!r}))
        asyncio.run(lock.acquire(timeout=5))
        print('locked', flush=True)
        time.sleep(60)
        """
    )
    holder = await asyncio.create_subprocess_exec(
        sys.executable, '-c', script, stdout=asyncio.subprocess.PIPE
    )
    try:
        line = await asyncio.wait_for(holder.stdout.readline(), 20)
        assert line.decode().strip() == 'locked'
        mine = MachineLock('t', tmp_path)
        with pytest.raises(MachineLockTimeout):
            await mine.acquire(timeout=0.3)
        holder.kill()  # no clean-up code of the holder runs
        await asyncio.wait_for(holder.wait(), 10)
        assert (tmp_path / '.vibe-seller-t.lock').exists()  # file left behind
        await mine.acquire(timeout=2)  # ...yet the lock is free
        mine.release()
    finally:
        if holder.returncode is None:
            holder.kill()
