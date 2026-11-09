"""Unit tests for the central sync scheduler (app/sync/scheduler.py).

Everything here runs without a database, a network or a running event
loop scheduler: ``app.sync.state`` is replaced by
``tests/fixtures/fake_sync_state.py`` and APScheduler by a recorder.
"""

import asyncio
from datetime import UTC, datetime, timedelta
import logging

import pytest

import app.browser.daemon_reaper as reaper
from app.env_options import Options
import app.scheduler.cron as cron
from app.sync import jobs as sync_jobs, state as sync_state
from app.sync.scheduler import (
    REASON_EVENT,
    REASON_MANUAL,
    REASON_TICK,
    SOURCE_EXTERNAL,
    SOURCE_GITHUB_MAIN,
    Cadence,
    SyncContext,
    SyncJob,
    SyncResult,
    SyncScheduler,
    cadence_seconds,
    offset_seconds,
    parse_cadence_overrides,
    timers_armed,
)
from tests.fixtures.fake_sync_state import FakeState

pytestmark = pytest.mark.unit


class FakeAPS:
    """Records add_job() calls instead of scheduling anything."""

    def __init__(self):
        self.added: dict[str, dict] = {}

    def add_job(self, func, **kwargs):
        self.added[kwargs['id']] = {**kwargs, 'func': func}

    def run_date(self, name: str) -> datetime:
        return self.added[f'sync:{name}']['run_date']


class Recorder:
    """A job body that records its calls and can be made to hang."""

    def __init__(self, result=None, error=None, hang=False):
        self.calls: list[SyncContext] = []
        self._result = result or SyncResult(status='updated')
        self._error = error
        self._hang = hang
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, ctx: SyncContext) -> SyncResult:
        self.calls.append(ctx)
        self.entered.set()
        if self._hang:
            await self.release.wait()
        if self._error is not None:
            raise self._error
        return self._result


@pytest.fixture(autouse=True)
def _armed_schedule(monkeypatch):
    """Every test here is about the schedule — arm it by default.

    The suite-wide ``VIBE_GITHUB_MAIN_SYNC=off`` (which keeps the
    GitHub-main pulls from ticking) is exercised explicitly below.
    """
    monkeypatch.delenv('VIBE_GITHUB_MAIN_SYNC', raising=False)
    monkeypatch.delenv('VIBE_EXTERNAL_SYNC', raising=False)


@pytest.fixture
def state(monkeypatch):
    return FakeState().install(monkeypatch)


@pytest.fixture
def aps():
    return FakeAPS()


@pytest.fixture
def sched(aps, state):
    return SyncScheduler(aps)


def job(name='skills', *, toggle_key=None, when=None, depends_on=(), **kw):
    return SyncJob(
        name=name,
        cadence=kw.pop('cadence', Cadence.DAILY),
        run=kw.pop('run', Recorder()),
        toggle_key=toggle_key,
        when=when,
        depends_on=depends_on,
        **kw,
    )


# ── registration ───────────────────────────────────────────────────


async def test_register_schedules_one_aps_job_per_sync_job(sched, aps):
    await sched.register(job('skills'))
    assert list(aps.added) == ['sync:skills']
    assert aps.added['sync:skills']['max_instances'] == 1
    assert aps.added['sync:skills']['coalesce'] is True
    assert sched.names() == ['skills']


async def test_duplicate_job_name_is_refused(sched):
    await sched.register(job('skills'))
    with pytest.raises(ValueError, match='duplicate sync job'):
        await sched.register(job('skills'))


async def test_plugin_sync_jobs_are_registered_alongside_core(
    monkeypatch, aps, state
):
    monkeypatch.delenv('VIBE_GITHUB_MAIN_SYNC', raising=False)
    monkeypatch.delenv('VIBE_EXTERNAL_SYNC', raising=False)
    plugin_job = job('plugin_thing', run=Recorder())
    monkeypatch.setattr(sync_jobs, 'registered_sync_jobs', lambda: [plugin_job])
    names = await sync_jobs.register_jobs(SyncScheduler(aps))
    assert set(names) == {
        'skills',
        'knowledge',
        'ads_skill',
        'presets',
        'vision_catalog',
        'model_pricing',
        'email',
        'daemon_reaper',
        'plugin_thing',
    }


# ── cadence + offset ───────────────────────────────────────────────


def test_cadence_tiers_are_the_documented_seconds():
    assert Cadence.FAST.value == 300
    assert Cadence.HOURLY.value == 3600
    assert Cadence.DAILY.value == 86400
    assert Cadence.BIDAILY.value == 172800


def test_cadence_override_parses_and_drops_garbage():
    assert parse_cadence_overrides('skills=20,presets=30') == {
        'skills': 20.0,
        'presets': 30.0,
    }
    assert parse_cadence_overrides('skills=20,oops,presets=-1,x=') == {
        'skills': 20.0,
    }
    assert parse_cadence_overrides('') == {}


def test_cadence_override_wins_over_the_tier(monkeypatch):
    monkeypatch.setenv('VIBE_SYNC_CADENCE_OVERRIDE', 'skills=20')
    assert cadence_seconds('skills', Cadence.DAILY) == 20.0
    assert cadence_seconds('knowledge', Cadence.DAILY) == 86400.0


def test_offset_is_stable_per_install_and_job(monkeypatch):
    monkeypatch.setattr(
        'app.sync.scheduler.stable_install_id', lambda: 'install-a'
    )
    first = offset_seconds('skills', Cadence.DAILY)
    assert offset_seconds('skills', Cadence.DAILY) == first
    assert 0 <= first < 3600
    monkeypatch.setattr(
        'app.sync.scheduler.stable_install_id', lambda: 'install-b'
    )
    assert offset_seconds('skills', Cadence.DAILY) != first


def test_offset_is_zero_without_a_jitter_window():
    assert offset_seconds('email', Cadence.FAST) == 0
    assert offset_seconds('anything', Cadence.HOURLY) == 0


def test_offset_zeroed_by_the_jitter_knob(monkeypatch):
    monkeypatch.setenv('VIBE_SYNC_JITTER', '0')
    assert offset_seconds('skills', Cadence.DAILY) == 0


def test_offset_spreads_over_the_window(monkeypatch):
    """Different installs land on different seconds across the hour."""
    seen = set()
    for i in range(10000):
        monkeypatch.setattr(
            'app.sync.scheduler.stable_install_id',
            (lambda n: lambda: f'install-{n}')(i),
        )
        seen.add(offset_seconds('skills', Cadence.DAILY))
    assert len(seen) > 2500  # ~uniform over 3600 slots
    assert max(seen) < 3600


# ── next_run_time ──────────────────────────────────────────────────


async def test_never_run_fires_now(sched, state, aps):
    await sched.register(job('skills'))
    run_at = aps.run_date('skills')
    assert run_at <= datetime.now(UTC) + timedelta(seconds=2)
    assert run_at > datetime.now(UTC) - timedelta(seconds=2)


async def test_next_run_is_last_ok_at_plus_cadence_plus_offset(
    sched, state, aps, monkeypatch
):
    monkeypatch.setenv('VIBE_SYNC_JITTER', '0')
    monkeypatch.setenv('VIBE_SYNC_CADENCE_OVERRIDE', 'skills=3600')
    base = datetime.now(UTC) - timedelta(minutes=5)
    state.seed('skills', last_ok_at=base.isoformat())
    await sched.register(job('skills'))
    assert aps.run_date('skills') == base + timedelta(seconds=3600)


async def test_overdue_job_keeps_its_past_run_time(sched, state, aps):
    """Overdue means "fire now" — the run date stays in the past."""
    base = datetime.now(UTC) - timedelta(days=3)
    state.seed('skills', last_ok_at=base.isoformat())
    await sched.register(job('skills'))
    assert aps.run_date('skills') < datetime.now(UTC)


async def test_a_run_that_did_not_move_last_ok_at_does_not_spin(
    sched, state, aps
):
    """A skipped run must re-arm a cadence from now, not re-fire."""
    state.seed('skills', last_ok_at='1999-01-01T00:00:00+00:00')
    rec = Recorder(result=SyncResult.skipped('toggle off'))
    await sched.register(job('skills', toggle_key='k', run=rec))
    state.toggles['k'] = False
    await sched.trigger('skills', reason=REASON_TICK)
    assert aps.run_date('skills') > datetime.now(UTC)


# ── single-flight ──────────────────────────────────────────────────


class SplitRecorder:
    """Separates what a tick does from what a manual run does."""

    def __init__(self, tick=None, manual=None):
        self.tick = tick or Recorder(hang=True)
        self.manual = manual or Recorder(
            result=SyncResult(status='updated', version='c' * 40)
        )

    async def __call__(self, ctx: SyncContext) -> SyncResult:
        if ctx.reason == REASON_TICK:
            return await self.tick(ctx)
        return await self.manual(ctx)


async def test_concurrent_ticks_share_one_run(sched, state):
    rec = Recorder(hang=True)
    await sched.register(job('skills', run=rec))
    task = asyncio.create_task(sched.trigger('skills', reason=REASON_TICK))
    await rec.entered.wait()
    others = [
        asyncio.create_task(sched.trigger('skills', reason=REASON_TICK))
        for _ in range(4)
    ]
    await asyncio.sleep(0)
    rec.release.set()
    results = [await task] + [await t for t in others]
    assert len(rec.calls) == 1
    assert all(r is results[0] for r in results)


async def test_a_strong_trigger_waits_out_the_run_in_flight(sched, state):
    """Manual/force wants work done, not whatever the tick is doing.

    The in-flight run may be a probe that says "unchanged" or a skip —
    a Sync button press must still produce a run of its own.
    """
    split = SplitRecorder(
        tick=Recorder(result=SyncResult.skipped('toggle off'), hang=True),
    )
    await sched.register(job('skills', run=split))
    ticking = asyncio.create_task(sched.trigger('skills', reason=REASON_TICK))
    await split.tick.entered.wait()

    manual = asyncio.create_task(sched.trigger('skills', reason=REASON_MANUAL))
    await asyncio.sleep(0)
    assert split.manual.calls == []  # still waiting the tick out
    split.tick.release.set()
    result = await manual

    assert result.status == 'updated'
    assert await ticking == SyncResult.skipped('toggle off')
    assert len(split.tick.calls) == 1
    assert len(split.manual.calls) == 1


async def test_concurrent_strong_triggers_share_the_fresh_run(sched, state):
    """Two Sync presses at once are one extra sync, not two."""
    split = SplitRecorder(tick=Recorder(hang=True))
    await sched.register(job('skills', run=split))
    ticking = asyncio.create_task(sched.trigger('skills', reason=REASON_TICK))
    await split.tick.entered.wait()

    press = [
        asyncio.create_task(sched.trigger('skills', reason=REASON_MANUAL))
        for _ in range(4)
    ]
    await asyncio.sleep(0)
    split.tick.release.set()
    results = [await task for task in press]
    await ticking

    assert len(split.tick.calls) == 1
    assert len(split.manual.calls) == 1
    assert all(r is results[0] for r in results)


async def test_a_failed_run_raises_for_every_awaiter(sched, state):
    rec = Recorder(error=RuntimeError('boom'), hang=True)
    await sched.register(job('skills', run=rec))
    task = asyncio.create_task(sched.trigger('skills', reason=REASON_TICK))
    await rec.entered.wait()
    other = asyncio.create_task(sched.trigger('skills', reason=REASON_TICK))
    await asyncio.sleep(0)
    rec.release.set()
    with pytest.raises(RuntimeError, match='boom'):
        await task
    with pytest.raises(RuntimeError, match='boom'):
        await other
    assert (await state.get_dict('skills'))['status'] == 'failed'


async def test_a_tick_failure_does_not_kill_the_scheduler(sched, state):
    rec = Recorder(error=RuntimeError('boom'))
    await sched.register(job('skills', run=rec))
    await sched._run_tick('skills')  # must not raise
    assert (await state.get_dict('skills'))['status'] == 'failed'


# ── toggle / precondition / dependencies ───────────────────────────


async def test_tick_honours_the_toggle(sched, state):
    rec = Recorder()
    await sched.register(
        job('skills', toggle_key='skills_auto_sync_enabled', run=rec)
    )
    state.toggles['skills_auto_sync_enabled'] = False
    result = await sched.trigger('skills', reason=REASON_TICK)
    assert result.status == 'skipped'
    assert result.skip_reason == 'toggle off'
    assert rec.calls == []
    assert (await state.get_dict('skills'))['last_ok_at'] is None


async def test_manual_run_bypasses_the_toggle(sched, state):
    rec = Recorder()
    await sched.register(job('skills', toggle_key='k', run=rec))
    state.toggles['k'] = False
    result = await sched.trigger('skills', reason=REASON_MANUAL)
    assert result.status == 'updated'
    assert len(rec.calls) == 1


async def test_a_toggle_read_error_is_treated_as_enabled(monkeypatch):
    """A DB hiccup must not silently stop pulling updates.

    The contract lives in app.sync.state.read_toggle — the scheduler
    just asks it — so the real function is what's under test here.
    """

    def boom():
        raise RuntimeError('database is on fire')

    monkeypatch.setattr(sync_state, 'async_session', boom)
    assert await sync_state.read_toggle('skills_auto_sync_enabled') is True
    assert await sync_state.read_toggle(None) is True


async def test_jobs_without_a_toggle_always_run(sched, state):
    rec = Recorder()
    await sched.register(job('knowledge', run=rec))
    result = await sched.trigger('knowledge', reason=REASON_TICK)
    assert result.status == 'updated'


async def test_false_precondition_skips_without_moving_last_ok_at(sched, state):
    async def not_ready():
        return False

    rec = Recorder()
    await sched.register(job('ads_skill', when=not_ready, run=rec))
    result = await sched.trigger('ads_skill')
    assert result.skip_reason == 'precondition'
    assert rec.calls == []
    assert (await state.get_dict('ads_skill'))['last_ok_at'] is None


async def test_depends_on_joins_an_in_flight_dependency(sched, state):
    slow = Recorder(hang=True)
    fast = Recorder()
    await sched.register(job('skills', run=slow))
    await sched.register(job('ads_skill', run=fast, depends_on=('skills',)))
    started = asyncio.create_task(sched.trigger('skills'))
    await slow.entered.wait()
    dependent = asyncio.create_task(sched.trigger('ads_skill'))
    await asyncio.sleep(0)
    assert fast.calls == []  # still waiting for skills
    slow.release.set()
    await started
    await dependent
    assert len(fast.calls) == 1


# ── ready() ────────────────────────────────────────────────────────


async def test_ready_returns_immediately_when_idle(sched, state):
    await sched.register(job('skills', run=Recorder()))
    await asyncio.wait_for(sched.ready(['skills']), timeout=0.5)


async def test_ready_waits_for_an_in_flight_run(sched, state):
    rec = Recorder(hang=True)
    await sched.register(job('skills', run=rec))
    running = asyncio.create_task(sched.trigger('skills'))
    await rec.entered.wait()
    waiter = asyncio.create_task(sched.ready(['skills']))
    await asyncio.sleep(0)
    assert not waiter.done()
    rec.release.set()
    await asyncio.wait_for(waiter, timeout=1)
    await running


async def test_ready_does_not_raise_a_failed_run(sched, state):
    rec = Recorder(error=RuntimeError('boom'), hang=True)
    await sched.register(job('skills', run=rec))
    running = asyncio.create_task(sched.trigger('skills'))
    await rec.entered.wait()
    waiter = asyncio.create_task(sched.ready(['skills']))
    rec.release.set()
    await asyncio.wait_for(waiter, timeout=1)
    with pytest.raises(RuntimeError):
        await running


async def test_ready_never_triggers_a_run(sched, state):
    rec = Recorder()
    await sched.register(job('skills', run=rec))
    await sched.ready(['skills', 'knowledge'])
    assert rec.calls == []


# ── shutdown drain ─────────────────────────────────────────────────


async def test_drain_cancels_and_awaits_in_flight_runs(sched, state):
    rec = Recorder(hang=True)
    await sched.register(job('skills', run=rec))
    running = asyncio.create_task(sched.trigger('skills'))
    await rec.entered.wait()
    await sched.drain()
    assert running.done()
    assert sched.is_running('skills') is False


# ── logging ────────────────────────────────────────────────────────


async def test_every_run_logs_one_greppable_line(sched, state, caplog):
    rec = Recorder(result=SyncResult(status='unchanged', version='c' * 40))
    await sched.register(job('skills', run=rec))
    with caplog.at_level(logging.INFO, logger='app.sync'):
        await sched.trigger('skills', reason=REASON_TICK)
    lines = [
        r.getMessage()
        for r in caplog.records
        if 'sync:skills' in r.getMessage()
    ]
    assert len(lines) == 1
    assert 'reason=tick' in lines[0]
    assert 'status=unchanged' in lines[0]
    assert 'version=cccccccccccc' in lines[0]


async def test_skip_reason_is_in_the_line(sched, state, caplog):
    await sched.register(job('skills', toggle_key='k', run=Recorder()))
    state.toggles['k'] = False
    with caplog.at_level(logging.INFO, logger='app.sync'):
        await sched.trigger('skills', reason=REASON_TICK)
    line = [
        r.getMessage()
        for r in caplog.records
        if 'sync:skills' in r.getMessage()
    ][0]
    assert 'status=skipped' in line
    assert 'skip=toggle off' in line


# ── the migrated jobs ─────────────────────────────────────────────


async def test_email_and_daemon_reaper_are_on_the_registry(
    aps, state, monkeypatch
):
    """M3: both left their own loops behind for the one scheduler."""
    monkeypatch.delenv('VIBE_GITHUB_MAIN_SYNC', raising=False)
    monkeypatch.delenv('VIBE_EXTERNAL_SYNC', raising=False)
    sched = SyncScheduler(aps)
    await sched.register_all(sync_jobs.core_jobs())
    jobs = sched.jobs
    assert jobs['email'].cadence is Cadence.FAST
    assert jobs['daemon_reaper'].cadence is Cadence.FAST
    assert jobs['email'].toggle_key is None  # per-account flag, not a toggle
    assert jobs['daemon_reaper'].toggle_key is None


async def test_nothing_registers_the_old_loops():
    """The while-loop and the extra APS email job are gone."""
    assert not hasattr(reaper, 'start_reaper_loop')
    assert not hasattr(cron, '_register_email_sync')


async def test_ready_times_out_instead_of_holding_a_launch(sched, state):
    """A hung download must not hold every task launch.

    The applies are atomic, so waiting only buys freshness: after the
    timeout the caller proceeds on the tree it has.
    """
    rec = Recorder(hang=True)
    await sched.register(job('skills', run=rec))
    running = asyncio.create_task(sched.trigger('skills'))
    await rec.entered.wait()

    await asyncio.wait_for(
        sched.ready(['skills'], timeout=0.05), timeout=1
    )  # returns; does not raise

    rec.release.set()
    await running


# ── the two switches: only outside sources can be stopped ─────────

GITHUB_MAIN_JOBS = {'skills', 'knowledge', 'presets', 'vision_catalog'}
EXTERNAL_JOBS = {'model_pricing', 'ads_skill', 'vision_catalog'}
SWITCHES = ('VIBE_GITHUB_MAIN_SYNC', 'VIBE_EXTERNAL_SYNC')


@pytest.fixture
def switches_on(monkeypatch):
    for name in SWITCHES:
        monkeypatch.delenv(name, raising=False)


def _stopped():
    return {j.name for j in sync_jobs.core_jobs() if not timers_armed(j)}


def test_each_job_names_its_outside_sources():
    jobs = {j.name: j.sources for j in sync_jobs.core_jobs()}
    assert {n for n, s in jobs.items() if SOURCE_GITHUB_MAIN in s} == (
        GITHUB_MAIN_JOBS
    )
    assert {n for n, s in jobs.items() if SOURCE_EXTERNAL in s} == (
        EXTERNAL_JOBS
    )
    assert jobs['email'] == frozenset()
    assert jobs['daemon_reaper'] == frozenset()


@pytest.mark.parametrize('value', ['off', 'OFF', '0', 'false'])
def test_github_main_switch_stops_only_github_main_pulls(
    switches_on, monkeypatch, value
):
    monkeypatch.setenv('VIBE_GITHUB_MAIN_SYNC', value)
    assert _stopped() == GITHUB_MAIN_JOBS


def test_external_switch_stops_only_outside_hosts(switches_on, monkeypatch):
    monkeypatch.setenv('VIBE_EXTERNAL_SYNC', 'off')
    assert _stopped() == EXTERNAL_JOBS


@pytest.mark.parametrize('value', ['', 'on', '1', 'true'])
def test_every_job_ticks_by_default(switches_on, monkeypatch, value):
    for name in SWITCHES:
        monkeypatch.setenv(name, value)
    assert _stopped() == set()


def test_no_env_var_can_stop_the_products_own_work(monkeypatch):
    """Mail polling and the daemon reaper have no switch: set every
    option the app reads to every 'off' spelling and they still tick."""
    own = [j for j in sync_jobs.core_jobs() if not j.sources]
    assert {j.name for j in own} == {'email', 'daemon_reaper'}
    for off in ('off', '0', 'false', '1'):
        for opt in Options:
            monkeypatch.setenv(opt.value[0], off)
        assert all(timers_armed(j) for j in own), off


async def test_a_stopped_job_still_runs_on_demand(monkeypatch, aps, state):
    monkeypatch.setenv('VIBE_GITHUB_MAIN_SYNC', 'off')
    rec = Recorder()
    sched = SyncScheduler(aps)
    await sched.register(
        job('skills', run=rec, sources=frozenset({SOURCE_GITHUB_MAIN}))
    )
    assert 'skills' not in sched._next
    await sched.trigger('skills', reason=REASON_EVENT, force=True)
    assert len(rec.calls) == 1
