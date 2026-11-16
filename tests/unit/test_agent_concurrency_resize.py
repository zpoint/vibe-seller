"""Changing max agent concurrency at runtime resizes the one limit.

Observed live: raising the limit from 2 to 5 in Settings left two
fan-out tasks that were already queued for an agent slot ``queued``
forever. ``set_max_concurrent`` swapped in a new semaphore; the queued
launches were waiting on the old one, and every later release went to
the new one, so nothing ever woke them.

Contract pinned here:
- raising the limit admits launches already queued, up to the new limit;
- lowering it stops no running agent and admits no new launch until
  in-flight drops below the new limit;
- in-flight accounting survives any sequence of resizes.
"""

import asyncio

import pytest

from app.ai import claude_backend_manager as mgr_mod
from app.ai.concurrency import ResizableLimiter

pytestmark = pytest.mark.unit


class _HeldSession:
    """An agent session that runs until the test lets it finish."""

    by_task: dict[str, '_HeldSession'] = {}

    def __init__(self, task_id, *_a, **_kw):
        self.task_id = task_id
        self.running = False
        self.session_id = None
        self.resume_session_id = None
        self._proc = None
        self._task = None
        self._catalog_read = False
        self._loaded_skills: set[str] = set()
        self._finish = asyncio.Event()
        _HeldSession.by_task[task_id] = self

    async def start(self):
        self.running = True
        self._task = asyncio.create_task(self._body())

    async def _body(self):
        await self._finish.wait()
        self.running = False

    async def stop(self):
        self._finish.set()


@pytest.fixture
def manager(monkeypatch):
    _HeldSession.by_task = {}

    async def _none(*_a, **_kw):
        return None

    async def _no_exclusions(*_a):
        return set()

    monkeypatch.setattr(mgr_mod, 'AgentSession', _HeldSession)
    monkeypatch.setattr(mgr_mod.skills_sync, 'wait_deps_ready', _none)
    monkeypatch.setattr(mgr_mod, '_ads_skill_exclusions', _no_exclusions)
    monkeypatch.setattr(
        mgr_mod.workspace_manager, 'prepare_task_workspace', _none
    )
    return mgr_mod.ClaudeCodeBackend()


async def _settle():
    for _ in range(20):
        await asyncio.sleep(0)


def _started():
    return {t for t, s in _HeldSession.by_task.items() if s.running}


async def _finish(task_id):
    _HeldSession.by_task[task_id]._finish.set()
    await _settle()


def _launch(manager, task_id):
    return asyncio.create_task(manager.run(task_id, 'p', no_store=True))


async def test_raising_the_limit_admits_launches_already_queued(manager):
    manager.set_max_concurrent(1)
    first = _launch(manager, 'a')
    await _settle()
    queued = [_launch(manager, t) for t in ('b', 'c', 'd')]
    await _settle()
    assert _started() == {'a'}

    manager.set_max_concurrent(3)
    await _settle()

    # Two queued launches start at once, with 'a' still running.
    assert _started() == {'a', 'b', 'c'}
    assert manager._in_flight == 3
    assert not queued[2].done()

    await _finish('a')
    assert _started() == {'b', 'c', 'd'}
    assert all([first.result(), *[q.result() for q in queued]])
    for t in ('b', 'c', 'd'):
        await _finish(t)
    assert manager._in_flight == 0


async def test_lowering_the_limit_keeps_running_and_caps_new(manager):
    manager.set_max_concurrent(3)
    for t in ('a', 'b', 'c'):
        _launch(manager, t)
    await _settle()
    assert _started() == {'a', 'b', 'c'}

    manager.set_max_concurrent(1)
    await _settle()
    # Nothing running is stopped.
    assert _started() == {'a', 'b', 'c'}

    late = _launch(manager, 'd')
    await _settle()
    assert 'd' not in _started()

    await _finish('a')
    await _finish('b')
    # in-flight (1) is not below the new limit (1): still queued.
    assert 'd' not in _started()
    assert not late.done()

    await _finish('c')
    assert _started() == {'d'}
    assert manager._in_flight == 1
    await _finish('d')
    assert manager._in_flight == 0


async def test_repeated_resizes_keep_one_count(manager):
    manager.set_max_concurrent(1)
    names = [f't{i}' for i in range(6)]
    for t in names:
        _launch(manager, t)
    await _settle()
    assert len(_started()) == 1

    for limit, expect in ((4, 4), (2, 4), (5, 5), (1, 5), (3, 5)):
        manager.set_max_concurrent(limit)
        await _settle()
        assert len(_started()) == expect, limit
        assert manager._in_flight == expect

    # Limit 3, five running, one queued: it waits for in-flight < 3.
    running = sorted(_started())
    await _finish(running[0])
    await _finish(running[1])
    assert len(_started()) == 3
    await _finish(running[2])
    assert len(_started()) == 3
    assert manager._in_flight == 3

    for t in sorted(_started()):
        await _finish(t)
    assert manager._in_flight == 0
    assert _started() == set()


class TestResizableLimiter:
    async def test_cancelled_waiter_leaves_no_slot_behind(self):
        lim = ResizableLimiter(1)
        await lim.acquire()
        waiter = asyncio.create_task(lim.acquire())
        await _settle()
        waiter.cancel()
        await _settle()
        assert lim.in_flight == 1 and lim.waiting == 0
        lim.release()
        assert lim.in_flight == 0

    async def test_granted_then_cancelled_hands_the_slot_on(self):
        lim = ResizableLimiter(1)
        await lim.acquire()
        cancelled = asyncio.create_task(lim.acquire())
        nxt = asyncio.create_task(lim.acquire())
        await _settle()
        lim.release()  # grants `cancelled` the slot...
        cancelled.cancel()  # ...but it is cancelled before resuming
        await _settle()
        assert nxt.done()
        assert lim.in_flight == 1

    async def test_waiters_are_admitted_in_arrival_order(self):
        lim = ResizableLimiter(1)
        await lim.acquire()
        order = []

        async def _take(name):
            await lim.acquire()
            order.append(name)

        tasks = [asyncio.create_task(_take(n)) for n in 'xyz']
        await _settle()
        lim.resize(2)
        await _settle()
        assert order == ['x']
        lim.release()
        lim.release()
        await _settle()
        assert order == ['x', 'y', 'z']
        await asyncio.gather(*tasks)

    async def test_release_without_acquire_is_an_error(self):
        lim = ResizableLimiter(2)
        with pytest.raises(RuntimeError):
            lim.release()

    def test_limit_is_at_least_one(self):
        lim = ResizableLimiter(0)
        assert lim.limit == 1
        lim.resize(-3)
        assert lim.limit == 1
