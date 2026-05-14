"""Unit tests for `_wait_for_session_end`'s event-driven contract.

Regression protection for the stop-retry race we fixed by replacing
the 1s polling loop with `asyncio.wait([done, plan_saved_event])`.
Each test exercises one state of the session registry and event
pair to pin down the invariant that the helper must never return
True for a session that has been superseded.
"""

import asyncio
from types import SimpleNamespace

import pytest

from app import task_session_lifecycle as task_runner_auto

pytestmark = pytest.mark.unit


class _FakeRegistry:
    """Stand-in for `agent_manager` — only the bits we touch."""

    def __init__(self):
        self.sessions: dict[str, object] = {}

    def get_session(self, task_id: str):
        return self.sessions.get(task_id)


def _make_session(*, auto_approve_plan: bool = False):
    return SimpleNamespace(
        done=asyncio.Event(),
        plan_saved_event=asyncio.Event(),
        auto_approve_plan=auto_approve_plan,
    )


@pytest.fixture
def registry(monkeypatch):
    reg = _FakeRegistry()
    monkeypatch.setattr(task_runner_auto, 'agent_manager', reg)
    return reg


async def test_none_session_returns_false(registry):
    # If the caller never captured a session, the helper must not
    # pretend the session ended successfully — that would let
    # `auto_run_task` fall through to finalize.
    assert await task_runner_auto._wait_for_session_end('t', None) is False


async def test_preset_done_returns_immediately(registry):
    # Session that finished *before* we began waiting (e.g. a
    # super-fast FakeAgent scenario, or a crashed subprocess whose
    # stream reader already drained) must resolve on first tick.
    s = _make_session()
    s.done.set()
    registry.sessions['t'] = s
    assert await task_runner_auto._wait_for_session_end('t', s) is True


async def test_done_wakes_waiter(registry):
    # The canonical happy path: waiter parked, session later
    # finishes, `done` fires, waiter resumes and confirms ownership.
    s = _make_session()
    registry.sessions['t'] = s

    async def finish():
        await asyncio.sleep(0)  # yield so the waiter starts
        s.done.set()

    _, result = await asyncio.gather(
        finish(), task_runner_auto._wait_for_session_end('t', s)
    )
    assert result is True


async def test_supersession_detected_when_registry_replaced(registry):
    # Stop-retry race: our session exits, a new pipeline registers a
    # fresh session under the same task_id before we re-read. The
    # helper must return False so the stale pipeline doesn't
    # clobber the retry's state machine.
    s_old = _make_session()
    s_new = _make_session()
    registry.sessions['t'] = s_old

    async def replace():
        await asyncio.sleep(0)
        registry.sessions['t'] = s_new
        s_old.done.set()

    _, result = await asyncio.gather(
        replace(), task_runner_auto._wait_for_session_end('t', s_old)
    )
    assert result is False


async def test_supersession_detected_when_registry_empty(registry):
    # FakeAgent-style cleanup: `stop()` pops the session before the
    # retry has registered its replacement. The helper still has to
    # detect this as a supersession; returning True here would let
    # auto_run_task proceed and overwrite the PENDING/QUEUED status
    # the retry endpoint just wrote.
    s = _make_session()
    registry.sessions['t'] = s

    async def pop():
        await asyncio.sleep(0)
        registry.sessions.pop('t')
        s.done.set()

    _, result = await asyncio.gather(
        pop(), task_runner_auto._wait_for_session_end('t', s)
    )
    assert result is False


async def test_plan_saved_event_returns_true_for_interactive(registry):
    # Interactive plan mode: the agent commits a plan and blocks on
    # user approval. `auto_run_task` must return so control passes
    # to `execute_planned_task`, even though the session is still
    # alive.
    s = _make_session(auto_approve_plan=False)
    registry.sessions['t'] = s

    async def save_plan():
        await asyncio.sleep(0)
        s.plan_saved_event.set()

    _, result = await asyncio.gather(
        save_plan(), task_runner_auto._wait_for_session_end('t', s)
    )
    assert result is True
    # Session is still the current one — caller will check status
    # in `_finalize_terminal_state` and hand off to execute_planned.
    assert registry.sessions['t'] is s


async def test_plan_saved_event_ignored_when_auto_approve(registry):
    # Scheduled / auto-mode sessions must NOT wake on plan-saved.
    # They run straight through; only actual session end should
    # unblock the waiter, otherwise we'd finalize mid-execution.
    s = _make_session(auto_approve_plan=True)
    registry.sessions['t'] = s
    s.plan_saved_event.set()  # set before waiter starts

    wait = asyncio.create_task(task_runner_auto._wait_for_session_end('t', s))
    # Give the waiter several loop ticks — it must NOT complete.
    for _ in range(10):
        await asyncio.sleep(0)
    assert not wait.done()

    # Unblock via the real termination signal.
    s.done.set()
    assert await wait is True


async def test_waiters_do_not_leak_on_supersession(registry):
    # When plan_saved wins the race, the done-waiter must be
    # cancelled and drained so we don't leave an orphan task that
    # later warns about unconsumed exceptions.
    s = _make_session(auto_approve_plan=False)
    registry.sessions['t'] = s
    s.plan_saved_event.set()

    # Snapshot the set of tasks before / after — we should create
    # two inside the helper and both should be cleaned up.
    before = set(asyncio.all_tasks())
    assert await task_runner_auto._wait_for_session_end('t', s) is True
    # Yield so any cancelled inner tasks finalize.
    await asyncio.sleep(0)
    leaked = set(asyncio.all_tasks()) - before - {asyncio.current_task()}
    # Inner tasks may briefly linger on the current loop tick.
    # Filter to ones that haven't completed.
    leaked = {t for t in leaked if not t.done()}
    assert not leaked, f'inner waiters leaked: {leaked}'
