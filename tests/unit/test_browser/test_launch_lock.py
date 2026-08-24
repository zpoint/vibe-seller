"""Unit test: the manager's global launch lock never waits for ever.

Regression guard for the 2026-08-24 hang. ``BrowserManager`` serializes
every launch on one lock so concurrent starts cannot hammer the shared
anti-detect client. That lock was a bare ``asyncio.Lock``, so a wedged
holder made every later caller wait indefinitely — and an HTTP handler
that waits indefinitely sends **no response at all**. What the task saw
was ``POST /api/stores/{id}/browser/start?force=1``, proxies unset,
returning ``HTTP 000 (240.003041s)``: no status, no message, nothing
naming what to restart, and no retry that could help because the next
call queued behind the same holder.

A hang is worse than a failure. These pin that the wait is bounded, that
the refusal says who is inside and for how long, and — the part that
makes it actionable — that it distinguishes ordinary contention from a
holder that is stuck.
"""

import asyncio
from unittest import mock

import pytest

from app.browser.launch_guards import BrowserBusyError, GlobalLaunchLock

pytestmark = pytest.mark.unit


def _wait(seconds: str, launch_cap: str = '180'):
    """Patch the two env options this policy reads."""
    return mock.patch.dict(
        'os.environ',
        {
            'VIBE_BROWSER_LOCK_WAIT_S': seconds,
            'VIBE_BROWSER_START_TIMEOUT_S': launch_cap,
        },
    )


async def test_an_uncontended_lock_is_taken_immediately():
    lock = GlobalLaunchLock()
    with _wait('5'):
        async with lock.hold('start_session(zuly)'):
            assert lock.holder == 'start_session(zuly)'
    assert lock.holder is None


async def test_a_wedged_holder_is_refused_not_waited_out():
    """The whole point: the second caller gets an answer."""
    lock = GlobalLaunchLock()
    released = asyncio.Event()

    async def wedged():
        async with lock.hold('start_session(zuly)'):
            await released.wait()

    task = asyncio.create_task(wedged())
    await asyncio.sleep(0)  # let it take the lock

    with _wait('0.05'), pytest.raises(BrowserBusyError) as err:
        async with lock.hold('start_session(infino)'):
            pass  # pragma: no cover

    assert 'start_session(zuly)' in str(err.value)
    assert 'start_session(infino)' in str(err.value)
    released.set()
    await task


async def test_the_lock_is_still_usable_after_a_refusal():
    """A refused waiter must not leave the lock acquired behind it."""
    lock = GlobalLaunchLock()
    released = asyncio.Event()

    async def wedged():
        async with lock.hold('stop_session(zuly)'):
            await released.wait()

    task = asyncio.create_task(wedged())
    await asyncio.sleep(0)
    with _wait('0.05'), pytest.raises(BrowserBusyError):
        async with lock.hold('peer'):
            pass  # pragma: no cover
    released.set()
    await task

    with _wait('5'):
        async with lock.hold('after'):
            assert lock.holder == 'after'


async def test_a_holder_inside_the_launch_budget_reads_as_contention():
    """Somebody is legitimately launching — the answer is "retry"."""
    lock = GlobalLaunchLock()
    released = asyncio.Event()

    async def holder():
        async with lock.hold('start_session(zuly)'):
            await released.wait()

    task = asyncio.create_task(holder())
    await asyncio.sleep(0)

    with _wait('0.05', launch_cap='180'), pytest.raises(BrowserBusyError) as e:
        async with lock.hold('peer'):
            pass  # pragma: no cover

    assert 'retry shortly' in str(e.value)
    assert 'restart Ziniao' not in str(e.value)
    released.set()
    await task


async def test_a_holder_past_the_launch_budget_reads_as_stuck():
    """Past the time a launch may take, waiting is not the answer."""
    lock = GlobalLaunchLock()
    released = asyncio.Event()

    async def holder():
        async with lock.hold('start_session(zuly)'):
            await released.wait()

    task = asyncio.create_task(holder())
    await asyncio.sleep(0.05)

    # A launch that may take at most 0.01s, held for ~0.05s already.
    with _wait('0.05', launch_cap='0.01'), pytest.raises(BrowserBusyError) as e:
        async with lock.hold('peer'):
            pass  # pragma: no cover

    assert 'restart Ziniao' in str(e.value)
    released.set()
    await task


async def test_no_launch_ceiling_means_no_verdict():
    """`BROWSER_START_TIMEOUT_S=0` says a launch may take for ever.

    So there is no duration that makes a holder *late*, and the old
    message read `within the 0s a launch may take` — the opposite of
    what 0 means. Report the fact, stop short of a verdict.
    """
    lock = GlobalLaunchLock()
    released = asyncio.Event()

    async def holder():
        async with lock.hold('start_session(zuly)'):
            await released.wait()

    task = asyncio.create_task(holder())
    await asyncio.sleep(0)

    with _wait('0.05', launch_cap='0'), pytest.raises(BrowserBusyError) as e:
        async with lock.hold('peer'):
            pass  # pragma: no cover

    msg = str(e.value)
    assert '0s a launch may take' not in msg
    assert 'retry shortly' not in msg and 'it is stuck' not in msg
    assert 'unbounded' in msg
    released.set()
    await task


async def test_zero_disables_the_ceiling():
    """An operator may opt back into waiting for ever; nothing else may."""
    lock = GlobalLaunchLock()
    released = asyncio.Event()
    took = asyncio.Event()

    async def holder():
        async with lock.hold('start_session(zuly)'):
            await released.wait()

    async def peer():
        with _wait('0'):
            async with lock.hold('peer'):
                took.set()

    task = asyncio.create_task(holder())
    await asyncio.sleep(0)
    waiter = asyncio.create_task(peer())
    await asyncio.sleep(0.05)

    assert not took.is_set()  # still waiting, not refused
    released.set()
    await asyncio.wait_for(asyncio.gather(task, waiter), timeout=2)
    assert took.is_set()


async def test_the_holder_is_forgotten_when_the_body_raises():
    lock = GlobalLaunchLock()
    with _wait('5'), pytest.raises(ValueError):
        async with lock.hold('start_session(zuly)'):
            raise ValueError('launch blew up')
    assert lock.holder is None
    assert lock.held_for() == 0.0
