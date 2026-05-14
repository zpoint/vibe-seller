"""Unit tests for the resume-failure retry helpers in
``app.task_session_lifecycle``.

These cover the orchestrator-owned retry path that replaces the old
hidden retry inside ``claude_backend_manager._release_on_done``. The
bug we're regressing against: ``execute_woken_task`` fell through to
FAILED on a stale ``--resume`` rejection because no orchestrator was
calling ``_maybe_retry_without_resume`` for that path.

Three layers are tested here as pure-ish functions; the orchestrator
wiring is covered separately in
``tests/workflow/test_wf_resume_retry.py``.
"""

import asyncio
from types import SimpleNamespace

import pytest

from app import task_session_lifecycle as lifecycle

pytestmark = pytest.mark.unit


def _session(
    *,
    rc: int | None,
    resume_id: str | None,
    result_text: str = '',
):
    """Build a stand-in session that exposes only the attributes
    `_is_resume_failure` reads. No full AgentSession needed.
    """
    proc = SimpleNamespace(returncode=rc) if rc is not None or rc == 0 else None
    if rc is None:
        # Pre-spawn: real AgentSession has _proc=None.
        proc = None
    return SimpleNamespace(
        _proc=proc,
        resume_session_id=resume_id,
        _result_text=result_text,
    )


# ── _is_resume_failure ──────────────────────────────────


def test_is_resume_failure_none_session():
    assert lifecycle._is_resume_failure(None) is False


def test_is_resume_failure_no_proc():
    s = _session(rc=None, resume_id='abc')
    assert lifecycle._is_resume_failure(s) is False


def test_is_resume_failure_running_proc():
    s = SimpleNamespace(
        _proc=SimpleNamespace(returncode=None),
        resume_session_id='abc',
        _result_text='',
    )
    assert lifecycle._is_resume_failure(s) is False


def test_is_resume_failure_clean_exit():
    s = _session(rc=0, resume_id='abc')
    assert lifecycle._is_resume_failure(s) is False


def test_is_resume_failure_no_resume_id():
    s = _session(rc=1, resume_id=None)
    assert lifecycle._is_resume_failure(s) is False


def test_is_resume_failure_has_result():
    s = _session(rc=1, resume_id='abc', result_text='partial output')
    assert lifecycle._is_resume_failure(s) is False


def test_is_resume_failure_match():
    """The exact pattern: rc!=0, resume was attempted, no result."""
    s = _session(rc=1, resume_id='abc')
    assert lifecycle._is_resume_failure(s) is True


# ── _maybe_retry_without_resume ─────────────────────────


class _StubManager:
    """Minimal agent_manager stand-in for the helper."""

    def __init__(self, *, retry_returns: bool = True):
        self.retry_returns = retry_returns
        self.retry_calls: list[str] = []
        self.next_session = SimpleNamespace(name='retry-session')

    async def retry_without_resume(self, task_id: str) -> bool:
        self.retry_calls.append(task_id)
        return self.retry_returns

    def get_session(self, task_id: str):
        return self.next_session


@pytest.fixture
def patched_manager(monkeypatch):
    mgr = _StubManager()
    monkeypatch.setattr(lifecycle, 'agent_manager', mgr)
    return mgr


@pytest.fixture
def patched_db(monkeypatch):
    """Stub `async_session` + DB ORM lookups so the helper's
    state-clear path runs end-to-end without a real database.
    """

    class _FakeTask:
        def __init__(self):
            self.session_id = 'old-sid'
            self.result = 'stale result'
            self.error = 'stale error'
            self.error_category = 'stale'
            self.updated_at = None

    fake_task = _FakeTask()

    class _FakeDB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, _model, _id):
            return fake_task

        async def commit(self):
            return None

    def _async_session():
        return _FakeDB()

    monkeypatch.setattr(lifecycle, 'async_session', _async_session)
    return fake_task


@pytest.mark.asyncio
async def test_maybe_retry_skips_when_not_resume_failure(
    patched_manager, patched_db
):
    s = _session(rc=0, resume_id='abc')  # clean exit → no retry
    assert await lifecycle._maybe_retry_without_resume('t1', s) is None
    assert patched_manager.retry_calls == []
    # Stale state must NOT be cleared on a non-retry path.
    assert patched_db.session_id == 'old-sid'
    assert patched_db.result == 'stale result'


@pytest.mark.asyncio
async def test_maybe_retry_clears_state_and_retries(
    patched_manager, patched_db
):
    s = _session(rc=1, resume_id='abc')  # the bug pattern
    new = await lifecycle._maybe_retry_without_resume('t1', s)
    assert new is patched_manager.next_session
    assert patched_manager.retry_calls == ['t1']
    # Stale state cleared so the post-retry finalizer doesn't see it.
    assert patched_db.session_id is None
    assert patched_db.result is None
    assert patched_db.error is None
    assert patched_db.error_category is None


@pytest.mark.asyncio
async def test_maybe_retry_returns_none_when_manager_refuses(
    patched_manager, patched_db
):
    patched_manager.retry_returns = False
    s = _session(rc=1, resume_id='abc')
    assert await lifecycle._maybe_retry_without_resume('t1', s) is None
    # Stale state was already cleared — that's OK; the next attempt
    # (manual retry) starts from a clean slate. Asserting state-clear
    # behavior would over-pin internal ordering.


# ── wait_for_session_with_retry ─────────────────────────


@pytest.mark.asyncio
async def test_wait_with_retry_returns_session_on_clean_finish(monkeypatch):
    """No resume failure → returns the original session, no retry."""
    sess = SimpleNamespace(name='s1')

    waits: list = []

    async def fake_wait(_tid, s):
        waits.append(s)
        return True

    async def fake_retry(_tid, _s):
        return None

    monkeypatch.setattr(lifecycle, '_wait_for_session_end', fake_wait)
    monkeypatch.setattr(lifecycle, '_maybe_retry_without_resume', fake_retry)

    out = await lifecycle.wait_for_session_with_retry('t1', sess)
    assert out is sess
    assert waits == [sess]  # only one wait — no retry


@pytest.mark.asyncio
async def test_wait_with_retry_threads_retry_session(monkeypatch):
    """Resume failure → retry kicks in → second wait → returns new session."""
    sess = SimpleNamespace(name='s1')
    retry_sess = SimpleNamespace(name='s2')

    waits: list = []

    async def fake_wait(_tid, s):
        waits.append(s)
        return True

    async def fake_retry(_tid, s):
        assert s is sess
        return retry_sess

    monkeypatch.setattr(lifecycle, '_wait_for_session_end', fake_wait)
    monkeypatch.setattr(lifecycle, '_maybe_retry_without_resume', fake_retry)

    out = await lifecycle.wait_for_session_with_retry('t1', sess)
    assert out is retry_sess
    assert waits == [sess, retry_sess]  # both sessions awaited


@pytest.mark.asyncio
async def test_wait_with_retry_bails_on_first_supersession(monkeypatch):
    """First wait returns False → orchestrator bails, no retry attempted."""
    sess = SimpleNamespace()
    retry_called = False

    async def fake_wait(_tid, _s):
        return False

    async def fake_retry(_tid, _s):
        nonlocal retry_called
        retry_called = True
        return None

    monkeypatch.setattr(lifecycle, '_wait_for_session_end', fake_wait)
    monkeypatch.setattr(lifecycle, '_maybe_retry_without_resume', fake_retry)

    out = await lifecycle.wait_for_session_with_retry('t1', sess)
    assert out is None
    assert retry_called is False


@pytest.mark.asyncio
async def test_wait_with_retry_bails_on_post_retry_supersession(monkeypatch):
    """Retry session was superseded by yet another retry → bail (None)."""
    sess = SimpleNamespace(name='s1')
    retry_sess = SimpleNamespace(name='s2')

    call_count = {'wait': 0}

    async def fake_wait(_tid, _s):
        call_count['wait'] += 1
        return call_count['wait'] == 1  # first True, second False

    async def fake_retry(_tid, _s):
        return retry_sess

    monkeypatch.setattr(lifecycle, '_wait_for_session_end', fake_wait)
    monkeypatch.setattr(lifecycle, '_maybe_retry_without_resume', fake_retry)

    out = await lifecycle.wait_for_session_with_retry('t1', sess)
    assert out is None
    assert call_count['wait'] == 2


@pytest.mark.asyncio
async def test_wait_with_retry_none_session_short_circuits():
    assert await lifecycle.wait_for_session_with_retry('t1', None) is None


# ── Real-event integration smoke test ───────────────────


@pytest.mark.asyncio
async def test_wait_with_retry_uses_real_done_event(monkeypatch):
    """Drive `_wait_for_session_end` via a real asyncio.Event so the
    helper's coroutine wiring (waiters / cancellation / supersession
    check) is exercised — not just the mocked layer above.
    """

    class _Reg:
        def __init__(self, sess):
            self.sess = sess

        def get_session(self, _tid):
            return self.sess

    sess = SimpleNamespace(
        done=asyncio.Event(),
        plan_saved_event=asyncio.Event(),
        auto_approve_plan=False,
    )
    reg = _Reg(sess)
    monkeypatch.setattr(lifecycle, 'agent_manager', reg)

    async def fake_retry(_tid, _s):
        return None  # simulate clean session — no retry needed

    monkeypatch.setattr(lifecycle, '_maybe_retry_without_resume', fake_retry)

    async def finish():
        await asyncio.sleep(0)  # let waiter park
        sess.done.set()

    out, _ = await asyncio.gather(
        lifecycle.wait_for_session_with_retry('t1', sess),
        finish(),
    )
    assert out is sess
