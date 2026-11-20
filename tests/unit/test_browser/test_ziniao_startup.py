"""Ziniao always comes up by itself: kill-all, 120 s deadline, one relaunch.

The user's spec: every automatic path relaunches Ziniao instead of
erroring; normal mode is simply killed and relaunched; there is no
``force`` switch; stray Ziniao processes are cleaned up before a launch
(Ziniao's own WebDriver doc, trap 3); a launch gets 120 s and requests
are not cut short (the doc asks for >=120 s per request).

Every scenario runs on ``FakeZiniaoHost`` — a fake machine at the
psutil/subprocess/httpx boundary — and calls the public
``ziniao_utils.ensure_ziniao_running`` exactly as callers do, so these
tests fail on the replaced code by behaviour (it raised on normal mode,
killed only ``ziniao.exe``, capped requests at 10 s, launched once).
"""

import asyncio
import threading
import time
from unittest import mock

import pytest

from tests.unit.test_browser.ziniao_fake_host import (
    LEFTOVERS,
    NORMAL_MODE,
    UPDATER_EXE,
    FakeZiniaoHost,
    ensure,
    installed,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _failed(host):
    return [p for e, p in host.events if e == 'browser_session_failed']


def _started(host):
    return [p for e, p in host.events if e == 'browser_session_started']


async def test_normal_mode_is_killed_and_relaunched_without_error():
    """Ziniao opened by hand (normal mode): no error, no prompt — every
    Ziniao process goes, then one WebDriver launch."""
    host = FakeZiniaoHost(api='down', procs=NORMAL_MODE)
    with installed(host):
        await ensure()
    assert len(host.launches) == 1
    killed = sorted(n for n, _ in host.killed)
    assert killed == ['env-kit.exe', 'ziniao.exe', 'ziniao.exe', 'ziniao.exe']
    assert host.api == 'webdriver'


async def test_relaunch_from_normal_mode_is_not_reported_as_a_failure():
    host = FakeZiniaoHost(api='down', procs=NORMAL_MODE)
    with installed(host):
        await ensure()
    assert _failed(host) == []
    [started] = _started(host)
    assert started['auto_launched'] is True
    assert started['prior_state'] == 'normal_mode'
    assert started['killed_processes'] == 4
    assert started['launch_attempts'] == 1


async def test_leftover_kernels_are_cleaned_up_before_launch():
    """No main client, but kernels (ziniaobrowser.exe, env-kit node.exe)
    left from a crash: they are killed before the launch, by path as
    well as by name. The updater's pending installer is never touched."""
    host = FakeZiniaoHost(api='down', procs=LEFTOVERS)
    updater = host.spawn('ziniao_x64_6.26.6.26_production.exe', UPDATER_EXE)
    with installed(host):
        await ensure()
    killed = sorted(n for n, _ in host.killed)
    assert killed == ['node.exe', 'ziniaobrowser.exe', 'ziniaobrowser.exe']
    assert updater in host.procs
    assert 'python.exe' in host.names()  # bystanders untouched
    [started] = _started(host)
    assert started['prior_state'] == 'leftovers'


async def test_a_slow_first_answer_gets_the_rest_of_the_deadline():
    """A cold client signs in on its first request: 30 s here. The old
    10 s request cap aborted it every time and the launch "timed out"; a
    request now carries everything left of the 120 s deadline."""
    host = FakeZiniaoHost(api='down', first_answer_s=30.0)
    with installed(host):
        await ensure()
    assert len(host.launches) == 1
    accepted = [r for r in host.requests if r['at'] >= host.launches[0] + 3]
    assert accepted[0]['read'] > 100  # ~117 s left, not 10 s


async def test_a_ready_client_is_seen_as_fast_as_before():
    """Launch → port binds at 3 s → ready well under 10 s (2 s polls)."""
    host = FakeZiniaoHost(api='down', ready_after=3.0)
    with installed(host):
        await ensure()
    [started] = _started(host)
    assert started['startup_s'] <= 5


async def test_a_launch_that_never_answers_is_killed_and_relaunched_once():
    host = FakeZiniaoHost(api='down', dead_launches=1)
    with installed(host):
        await ensure()
    assert len(host.launches) == 2
    assert host.launches[1] - host.launches[0] >= 120
    [started] = _started(host)
    assert started['launch_attempts'] == 2
    assert _failed(host) == []


async def test_two_dead_launches_fail_once_with_the_reason():
    host = FakeZiniaoHost(api='down', dead_launches=2)
    with installed(host), pytest.raises(RuntimeError) as e:
        await ensure()
    assert len(host.launches) == 2
    assert getattr(e.value, 'reason', None) == 'startup_timeout'
    [failed] = _failed(host)
    assert failed['error_category'] == 'startup_timeout'
    assert failed['last_poll_error'] == 'refused'
    assert failed['launch_attempts'] == 2
    assert failed['elapsed_s'] >= 240


async def test_concurrent_callers_share_one_kill_and_one_launch():
    """A fan-out finding Ziniao in normal mode: one round, not three
    (each round's kill-all would kill the previous one's launch)."""
    host = FakeZiniaoHost(api='down', procs=NORMAL_MODE)
    with installed(host):
        await asyncio.gather(ensure(), ensure(), ensure())
    assert len(host.launches) == 1
    assert sum(1 for n, _ in host.killed if n == 'ziniao.exe') == 3


async def test_an_answering_client_is_left_alone():
    host = FakeZiniaoHost(api='webdriver', procs=NORMAL_MODE)
    with installed(host):
        await ensure()
    assert host.launches == [] and host.killed == []


async def test_a_hung_client_with_live_store_browsers_is_not_killed():
    """Accepted but silent while this server has store browsers on it:
    its kernels still serve CDP, so killing it would take every running
    task down. Leave it; no error."""
    host = FakeZiniaoHost(api='hung', procs=NORMAL_MODE)
    with installed(host):
        await ensure(live=True)
    assert host.killed == [] and host.launches == []


async def test_a_hung_client_with_nothing_live_is_relaunched():
    host = FakeZiniaoHost(api='hung', procs=NORMAL_MODE)
    with installed(host):
        await ensure()
    assert len(host.launches) == 1
    [started] = _started(host)
    assert started['prior_state'] == 'unresponsive'


async def test_unkillable_processes_fail_fast_without_launching():
    host = FakeZiniaoHost(
        api='down', procs=NORMAL_MODE, unkillable=('ziniao.exe',)
    )
    with installed(host), pytest.raises(RuntimeError) as e:
        await ensure()
    assert host.launches == []
    assert getattr(e.value, 'reason', None) == 'kill_failed'


async def test_a_foreign_program_on_the_port_fails_fast():
    host = FakeZiniaoHost(api='down', procs=NORMAL_MODE, port_owner=777)
    with installed(host), pytest.raises(RuntimeError) as e:
        await ensure()
    assert host.launches == []
    assert getattr(e.value, 'reason', None) == 'port_in_use'


async def test_after_a_timeout_automatic_callers_do_not_start_another_round():
    """Cooldown: a fan-out or a retrying agent must not kill and relaunch
    over and over (and close a Ziniao the user opened to fix a login)."""
    host = FakeZiniaoHost(api='down', dead_launches=5)
    with installed(host):
        with pytest.raises(RuntimeError):
            await ensure()
        with pytest.raises(RuntimeError) as again:
            await ensure()
        assert len(host.launches) == 2  # no new round
        assert 'Not retried' in str(again.value)
        assert len(_failed(host)) == 1
        # A user clicking Refresh in store setup does try again.
        with pytest.raises(RuntimeError):
            await ensure(retry_failed=True)
        assert len(host.launches) == 4


async def test_wsl_kills_normal_mode_and_points_at_the_launcher():
    """WSL cannot launch Ziniao: it closes the normal-mode client through
    Windows interop and says to run the .bat — never launches."""
    host = FakeZiniaoHost(api='down', procs=NORMAL_MODE)
    with installed(host, wsl=True), pytest.raises(RuntimeError) as e:
        await ensure()
    assert host.launches == []
    assert sorted(n for n, _ in host.killed) == sorted(
        n for n, _ in NORMAL_MODE
    )
    assert 'ziniao_webdriver.bat' in str(e.value)
    assert getattr(e.value, 'reason', None) == 'wsl_not_running'


async def test_wsl_never_kills_a_listening_client_it_cannot_reach():
    """Windows shows a listener on the port but WSL cannot reach it: a
    networking problem. Killing would lose a healthy client only the
    .bat can bring back."""
    host = FakeZiniaoHost(api='down', procs=NORMAL_MODE, port_owner=555)
    with installed(host, wsl=True), pytest.raises(RuntimeError) as e:
        await ensure()
    assert host.killed == []
    assert getattr(e.value, 'reason', None) == 'wsl_unreachable'


async def test_a_caller_past_its_wait_gets_starting_and_the_round_goes_on():
    """An HTTP route must answer (the wrapper's curl gives up at 90 s):
    past its wait it gets a retryable "starting"; the round goes on and
    the first caller still gets Ziniao."""
    from app.browser.launch_guards import BrowserBusyError  # noqa: PLC0415

    host = FakeZiniaoHost(api='down', procs=NORMAL_MODE)
    host.block = threading.Event()
    with installed(host):
        first = asyncio.ensure_future(ensure())
        for _ in range(50):  # until the round is inside its launch
            await asyncio.sleep(0.01)
            if not any(n == 'ziniao.exe' for n in host.names()):
                break
        with pytest.raises(BrowserBusyError) as e:
            await ensure(wait_s=0.05)
        assert 'starting' in str(e.value)
        assert not first.done()
        host.block.set()
        await first
    assert len(host.launches) == 1


# ── The machine-wide lock: one restart at a time, never stuck ───────────


def _startup():
    from app.browser import ziniao_startup  # noqa: PLC0415

    return ziniao_startup


async def _until_waiting(state_get) -> None:
    for _ in range(200):
        await asyncio.sleep(0)
        st = state_get()
        if st is not None and st.current is not None:
            return


async def test_another_process_bringing_ziniao_up_is_waited_for_not_killed():
    """Another server (or the old process of a restart) holds the startup
    lock and brings Ziniao up: our round waits, looks again, and does not
    kill the client it just launched."""
    from app.browser.machine_lock import MachineLock  # noqa: PLC0415

    host = FakeZiniaoHost(api='down', procs=NORMAL_MODE)
    with installed(host):
        zs = _startup()
        other = MachineLock('ziniao-startup', zs._LOCK_DIR)
        await other.acquire(timeout=1)
        ours = asyncio.ensure_future(ensure())
        await _until_waiting(lambda: zs._state)
        # Real time for our round to reach the lock and start waiting.
        await asyncio.get_running_loop().run_in_executor(None, time.sleep, 0.6)
        assert not ours.done()
        host.api = 'webdriver'  # the other process's launch came up
        other.release()
        await asyncio.wait_for(ours, 10)
    assert host.killed == [] and host.launches == []


async def test_a_lock_held_for_the_whole_wait_fails_and_kills_nothing():
    from app.browser.machine_lock import MachineLock  # noqa: PLC0415

    host = FakeZiniaoHost(api='down', procs=NORMAL_MODE)
    with installed(host):
        zs = _startup()
        other = MachineLock('ziniao-startup', zs._LOCK_DIR)
        await other.acquire(timeout=1)
        try:
            with (
                mock.patch.object(zs, 'ROUND_CAP_S', 0.5),
                pytest.raises(RuntimeError) as e,
            ):
                await ensure()
        finally:
            other.release()
    assert getattr(e.value, 'reason', None) == 'other_process_starting'
    assert host.killed == [] and host.launches == []


async def test_a_stuck_round_is_stopped_frees_the_lock_and_the_next_start_works():
    """'不能无限锁住': a round that hangs (here: a launch that never
    returns) hits the hard cap, gives the lock back, and the next caller
    starts Ziniao normally."""
    from app.browser.machine_lock import MachineLock  # noqa: PLC0415

    host = FakeZiniaoHost(api='down', procs=NORMAL_MODE)
    host.block = threading.Event()
    with installed(host):
        zs = _startup()
        try:
            with (
                mock.patch.object(zs, 'ROUND_CAP_S', 0.5),
                pytest.raises(RuntimeError) as e,
            ):
                await ensure()
            assert getattr(e.value, 'reason', None) == 'round_stuck'
            probe = MachineLock('ziniao-startup', zs._LOCK_DIR)
            await probe.acquire(timeout=1)  # free, not held for ever
            probe.release()
        finally:
            host.block.set()
        await ensure()
    assert host.api == 'webdriver'


async def test_a_client_brought_up_just_before_our_round_is_not_killed():
    """The race the lock alone leaves open: another process finished its
    launch and let go just before our round took the lock. We still look
    first — the probe that sent us here is out of date."""
    host = FakeZiniaoHost(api='down', procs=NORMAL_MODE)
    with installed(host):
        zs = _startup()
        real_acquire = zs.MachineLock.acquire

        async def acquire_after_other_finished(self, timeout):
            host.api = 'webdriver'  # came up while we were on our way
            return await real_acquire(self, timeout)

        with mock.patch.object(
            zs.MachineLock, 'acquire', acquire_after_other_finished
        ):
            await ensure()
    assert host.killed == [] and host.launches == []
