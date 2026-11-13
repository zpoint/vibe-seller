"""Tests for app/auto_update — the in-app update state machine.

Platform branching is exercised by faking ``sys.platform`` (the repo's
established pattern in test_windows_update*), and the boundaries that
would otherwise run a process or reach a network are patched at the
module they are called through (``wu.check_for_update``,
``pip_update.warm``, …). Everything else — path choice, staging, the
state transitions, the idle gate, the events — runs for real.

The decision under test is the one the feature is named for: an update
that changes only the wheel applies itself when the machine is idle, and
an update that changes the bundled runtime does not. Both halves are
pinned, in both directions.
"""

import asyncio
import json
from pathlib import Path
import time
from unittest.mock import patch

import pytest

from app import pip_update, upgrade_restart
import app.auto_update as au
from app.telemetry_events import TelemetryEvent
import app.windows_update as wu

pytestmark = pytest.mark.unit

_BLOB = b'installer-bytes' * 100
PIP = pip_update.PATH_PIP
INSTALLER = pip_update.PATH_INSTALLER
_FP = 'f' * 64
_OTHER = '0' * 64


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """A cold machine: no staging dir, no ready clock, no record — all
    three live under ``<VIBE_HOME>/updates`` and all three are real
    files whose absence/presence is part of the behaviour."""
    au.reset_for_tests()
    updates = tmp_path / 'updates'
    monkeypatch.setattr(wu, 'updates_root', lambda: updates)
    monkeypatch.setattr(
        au.update_idle, 'ready_file', lambda: updates / 'update-ready.json'
    )
    monkeypatch.setattr(au.pip_update, 'updates_dir', lambda: updates)
    monkeypatch.setattr(
        au.pip_update, 'record_path', lambda: updates / 'last.json'
    )
    monkeypatch.setattr(
        au.pip_update, 'record_mtime', lambda: _mtime(updates / 'last.json')
    )
    monkeypatch.setattr(au, 'APPLY_WATCH_INTERVAL_SECONDS', 0.01)
    monkeypatch.setattr(au.update_idle, 'IDLE_CHECK_INTERVAL_SECONDS', 0.01)
    # An apply that never reports must give up in the test, not sit out
    # the production fifteen-minute budget.
    monkeypatch.setattr(au.pip_update, 'APPLY_TIMEOUT_SECONDS', 0.05)
    yield
    au.reset_for_tests()


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _record_events(monkeypatch) -> list[tuple[str, dict]]:
    """Catch what the update flow reports.

    The sending lives in ``app/update_events.py`` (the state machine is
    at its line budget and reporting is a separate job), so that is the
    module whose telemetry handle gets replaced — patching
    ``app.auto_update`` would silently record nothing and every event
    assertion below would be vacuous.
    """
    events: list[tuple[str, dict]] = []

    def fake_send(event, properties=None):
        events.append((event, dict(properties or {})))

    monkeypatch.setattr(au.update_events.telemetry, 'send', fake_send)
    return events


def _pypi(tmp_path, monkeypatch, doc, current):
    src = tmp_path / 'pypi.json'
    src.write_text(json.dumps(doc), encoding='utf-8')
    monkeypatch.setenv('VIBE_SELLER_PYPI_JSON_URL', str(src))
    monkeypatch.setattr(au, 'get_version', lambda: current)


class FakeProc:
    """The detached helper, as far as the watcher can tell: still running,
    so nothing has been reported and there is nothing to conclude."""

    def poll(self):
        return None


class Queue:
    def __init__(self):
        self.paused = False
        self.log: list[str] = []

    def pause_dispatch(self):
        self.paused = True
        self.log.append('pause')

    def resume_dispatch(self):
        self.paused = False
        self.log.append('resume')


class Agents:
    """``live_agent_count`` counts calls, so a test can say "a task
    started after the idle answer" without a thread."""

    def __init__(self, sequence=None):
        self.sequence = sequence
        self.calls = 0

    def live_agent_count(self):
        i = self.calls
        self.calls += 1
        if self.sequence is None:
            return 0
        return self.sequence[min(i, len(self.sequence) - 1)]


# ── Windows: the path choice ─────────────────────────────────────────


@pytest.fixture(name='windows')
def _windows(monkeypatch, tmp_path):
    """A native-Windows installer bundle. The ``bundle.json`` beside the
    venv is what the update check compares against, so tests place it
    with :func:`_installed`."""
    app = tmp_path / 'app'
    (app / '.venv').mkdir(parents=True)
    (app / 'unins000.exe').write_bytes(b'')
    monkeypatch.setattr(au.sys, 'platform', 'win32')
    monkeypatch.setattr(au.sys, 'prefix', str(app / '.venv'))
    monkeypatch.setattr(au, 'get_version', lambda: '0.1.0')
    return app


def _installed(monkeypatch, app: Path, fingerprint):
    path = app / 'bundle.json'
    path.unlink(missing_ok=True)
    if fingerprint is not None:
        path.write_text(
            json.dumps({'fingerprint': fingerprint}), encoding='utf-8'
        )
    # app.bundle, reached through the module that asks it: the
    # Windows path decision lives in app/update_windows.py now.
    monkeypatch.setattr(
        au.update_windows.bundle, 'bundle_file', lambda root=None: path
    )


def _offer(app: Path, release_fp=_FP):
    src = app / 'setup.exe'
    src.write_bytes(_BLOB)
    offer = {
        'version': '0.2.0',
        'url': str(src),
        'size': len(_BLOB),
        'digest': None,
    }
    if release_fp is not None:
        offer['bundle'] = {'url': 'mock://bundle', 'size': None, 'digest': None}
    return offer


def _pip_leg(monkeypatch, app, release_fp=_FP, offline_reason=None):
    """Wire a Windows cycle to *release_fp* and an offline verdict."""
    _installed(monkeypatch, app, _FP)
    offer = _offer(app, release_fp)
    monkeypatch.setattr(wu, 'check_for_update', lambda: offer)
    monkeypatch.setattr(
        wu,
        'fetch_bundle',
        lambda o: None if release_fp is None else {'fingerprint': release_fp},
    )
    touched = {'warm': 0, 'browser': 0, 'staged': 0}
    monkeypatch.setattr(
        au.pip_update,
        'warm',
        lambda v: touched.__setitem__('warm', touched['warm'] + 1),
    )
    monkeypatch.setattr(
        au.pip_update,
        'pre_install_browser',
        lambda v: touched.__setitem__('browser', touched['browser'] + 1),
    )
    monkeypatch.setattr(
        au.pip_update, 'offline_apply_proven', lambda v: offline_reason
    )
    real_stage = wu.stage_installer

    def stage(offer, progress=None):
        touched['staged'] += 1
        return real_stage(offer, progress=progress)

    monkeypatch.setattr(wu, 'stage_installer', stage)
    return touched


class TestWindowsPathChoice:
    async def test_equal_fingerprints_take_the_pip_path(
        self, windows, monkeypatch
    ):
        touched = _pip_leg(monkeypatch, windows)
        _record_events(monkeypatch)
        await au.run_cycle()
        snap = au.snapshot()
        assert snap['state'] == au.READY
        assert snap['path'] == PIP
        assert snap['auto_apply'] is True
        assert snap['popup_due'] is False
        assert touched['warm'] == 1
        assert touched['staged'] == 0, (
            'downloaded a 700 MB installer it did not need'
        )
        assert not (wu.updates_root() / '0.2.0').exists()

    async def test_different_fingerprints_take_the_installer(
        self, windows, monkeypatch
    ):
        touched = _pip_leg(monkeypatch, windows, release_fp=_OTHER)
        _record_events(monkeypatch)
        await au.run_cycle()
        snap = au.snapshot()
        assert snap['state'] == au.READY
        assert snap['path'] == INSTALLER
        assert snap['auto_apply'] is False
        assert snap['popup_due'] is True
        # A bundle change is exactly the case the pip path must not touch.
        assert touched['warm'] == 0
        assert touched['staged'] == 1
        assert (wu.updates_root() / '0.2.0' / 'VibeSeller-Setup.exe').exists()

    async def test_an_install_without_bundle_json_takes_the_installer(
        self, windows, monkeypatch
    ):
        """0.3.1 predates bundle.json, so there is nothing to compare:
        it can only ever be installed over."""
        touched = _pip_leg(monkeypatch, windows)
        _installed(monkeypatch, windows, None)
        _record_events(monkeypatch)
        await au.run_cycle()
        assert au.snapshot()['path'] == INSTALLER
        assert touched['warm'] == 0

    async def test_a_release_without_the_asset_takes_the_installer(
        self, windows, monkeypatch
    ):
        touched = _pip_leg(monkeypatch, windows, release_fp=None)
        _record_events(monkeypatch)
        await au.run_cycle()
        assert au.snapshot()['path'] == INSTALLER
        assert touched['warm'] == 0

    async def test_an_unreadable_release_bundle_is_the_installer(
        self, windows, monkeypatch
    ):
        """A source that will not answer is "unknown", and unknown means
        the path that is always correct."""
        touched = _pip_leg(monkeypatch, windows)
        _installed(monkeypatch, windows, _FP)
        offer = _offer(windows)
        monkeypatch.setattr(wu, 'check_for_update', lambda: offer)

        def boom(_o):
            raise RuntimeError('github is down')

        monkeypatch.setattr(wu, 'fetch_bundle', boom)
        _record_events(monkeypatch)
        await au.run_cycle()
        assert au.snapshot()['path'] == INSTALLER
        assert touched['warm'] == 0

    async def test_the_offline_dry_run_decides_readiness(
        self, windows, monkeypatch
    ):
        """``ready`` means "applying this needs no network", which is what
        makes an unattended restart at 03:00 safe. A dry run that would
        still fetch something is not that promise — and the release is
        not lost, it goes to the installer."""
        touched = _pip_leg(
            monkeypatch, windows, offline_reason='would fetch a wheel'
        )
        events = _record_events(monkeypatch)
        await au.run_cycle()
        snap = au.snapshot()
        assert snap['path'] == INSTALLER
        assert snap['state'] == au.READY
        assert touched['staged'] == 1
        downloaded = [
            e for e in events if e[0] == TelemetryEvent.UPDATE_DOWNLOADED
        ]
        assert [e[1]['outcome'] for e in downloaded] == ['installer', 'ok']

    async def test_a_version_pypi_does_not_have_yet_is_retried(
        self, windows, monkeypatch
    ):
        """GitHub's release can exist before the index carries the
        wheels. That is a six-hour retry, not a permanent error."""

        def cold(_v):
            raise pip_update.PrefetchError('no such version')

        _pip_leg(monkeypatch, windows)
        monkeypatch.setattr(au.pip_update, 'warm', cold)
        _record_events(monkeypatch)
        await au.run_cycle()
        assert au.snapshot()['state'] == au.ERROR
        assert 'no such version' in au.snapshot()['error']


# ── Windows: the installer leg, unchanged from #244 ─────────────────


def _make_local_stage(version='0.2.0', blob=_BLOB):
    import hashlib  # noqa: PLC0415 — test-local

    src = Path(wu.updates_root()) / 'src.exe'
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(blob)
    wu.stage_installer({
        'version': version,
        'url': str(src),
        'size': len(blob),
        'digest': 'sha256:' + hashlib.sha256(blob).hexdigest(),
    })


class TestWindowsInstallerLeg:
    async def test_download_then_ready(self, windows, monkeypatch):
        _installed(monkeypatch, windows, None)
        offer = _offer(windows, release_fp=None)
        monkeypatch.setattr(wu, 'check_for_update', lambda: offer)
        _record_events(monkeypatch)
        await au.run_cycle()
        snap = au.snapshot()
        assert snap['state'] == au.READY
        assert snap['version'] == '0.2.0'

    async def test_boot_with_a_verified_stage_is_ready_without_downloading(
        self, windows, monkeypatch
    ):
        _make_local_stage()
        monkeypatch.setattr(
            wu,
            'check_for_update',
            lambda: (_ for _ in ()).throw(RuntimeError('offline')),
        )
        await au.run_cycle()
        snap = au.snapshot()
        assert snap['state'] == au.READY
        assert snap['version'] == '0.2.0'
        assert snap['path'] == INSTALLER

    async def test_a_missing_source_is_an_error_with_the_stage(
        self, windows, monkeypatch
    ):
        monkeypatch.setattr(
            wu,
            'check_for_update',
            lambda: {
                'version': '0.2.0',
                'url': '/nonexistent/VibeSeller-Setup.exe',
                'size': 10,
                'digest': None,
            },
        )
        events = _record_events(monkeypatch)
        await au.run_cycle()
        assert au.snapshot()['state'] == au.ERROR
        evs = [e for e in events if e[0] == TelemetryEvent.UPDATE_DOWNLOADED]
        assert evs and evs[0][1]['outcome'] == 'failed'
        assert evs[0][1]['stage'] in ('download', 'verify')
        assert evs[0][1]['path'] == INSTALLER

    async def test_event_send_failure_never_breaks_the_cycle(
        self, windows, monkeypatch
    ):
        offer = _offer(windows, release_fp=None)
        monkeypatch.setattr(wu, 'check_for_update', lambda: offer)
        monkeypatch.setattr(au.update_events.telemetry, 'send', _boom)
        await au.run_cycle()
        assert au.snapshot()['state'] == au.READY


def _boom(*_a, **_k):
    raise RuntimeError('events are down')


# ── macOS / Linux ────────────────────────────────────────────────────


class TestUnixCycle:
    async def test_stable_is_never_offered_a_nightly(
        self, tmp_path, monkeypatch
    ):
        _pypi(
            tmp_path,
            monkeypatch,
            {
                'info': {'version': '0.9.0'},
                'releases': {
                    '0.8.0': [{'yanked': False}],
                    '0.9.0': [{'yanked': False}],
                    '0.10.0.dev202611010000': [{'yanked': False}],
                },
            },
            '0.8.0',
        )
        ran = []
        monkeypatch.setattr(
            au.pip_update, 'warm', lambda v: ran.append(('warm', v))
        )
        monkeypatch.setattr(
            au.pip_update,
            'pre_install_browser',
            lambda v: ran.append(('browser', v)),
        )
        monkeypatch.setattr(
            au.pip_update,
            'keep_rollback_warm',
            lambda: ran.append(('rollback',)),
        )
        events = _record_events(monkeypatch)
        await au._cycle_unix()
        snap = au.snapshot()
        assert snap['state'] == au.READY
        assert snap['version'] == '0.9.0'
        # No installer to fingerprint here: always the pip path, always
        # with the target's browser fetched up front so the restart
        # downloads nothing big.
        assert snap['path'] == PIP
        assert snap['auto_apply'] is True
        assert ran == [('warm', '0.9.0'), ('browser', '0.9.0'), ('rollback',)]
        evs = [e for e in events if e[0] == TelemetryEvent.UPDATE_DOWNLOADED]
        assert evs[0][1]['outcome'] == 'ok'
        assert evs[0][1]['path'] == PIP

    async def test_nightly_moves_to_the_highest_newer(
        self, tmp_path, monkeypatch
    ):
        _pypi(
            tmp_path,
            monkeypatch,
            {
                'info': {'version': '0.9.0'},
                'releases': {
                    '0.9.0': [{'yanked': False}],
                    '0.10.0.dev202611010000': [{'yanked': False}],
                },
            },
            '0.10.0.dev202610100000',
        )
        ran = []
        monkeypatch.setattr(au.pip_update, 'warm', lambda v: ran.append(v))
        monkeypatch.setattr(
            au.pip_update, 'pre_install_browser', lambda v: None
        )
        monkeypatch.setattr(au.pip_update, 'keep_rollback_warm', lambda: None)
        _record_events(monkeypatch)
        await au._cycle_unix()
        assert au.snapshot()['version'] == '0.10.0.dev202611010000'
        assert ran == ['0.10.0.dev202611010000']

    async def test_a_failed_prefetch_is_an_error_not_a_ready(
        self, tmp_path, monkeypatch
    ):
        _pypi(
            tmp_path,
            monkeypatch,
            {'info': {'version': '0.9.0'}, 'releases': {}},
            '0.8.0',
        )

        def cold(_v):
            raise RuntimeError('no network')

        monkeypatch.setattr(au.pip_update, 'warm', cold)
        events = _record_events(monkeypatch)
        await au._cycle_unix()
        snap = au.snapshot()
        assert snap['state'] == au.ERROR
        assert 'no network' in snap['error']
        evs = [e for e in events if e[0] == TelemetryEvent.UPDATE_DOWNLOADED]
        assert evs[0][1]['outcome'] == 'failed'
        assert evs[0][1]['stage'] == 'prefetch'

    async def test_up_to_date_is_idle(self, tmp_path, monkeypatch):
        _pypi(
            tmp_path,
            monkeypatch,
            {'info': {'version': '0.8.0'}, 'releases': {}},
            '0.8.0',
        )
        await au._cycle_unix()
        assert au.snapshot()['state'] == au.IDLE


class TestDevNeverUpdates:
    """S7, the state machine's half. The channel rules themselves are in
    test_release_channel.py; the release sources refuse dev in
    test_update_check.py and test_windows_update.py."""

    async def test_a_dev_build_is_never_offered_a_version(
        self, tmp_path, monkeypatch
    ):
        _pypi(
            tmp_path,
            monkeypatch,
            {'info': {'version': '9.9.9'}, 'releases': {'9.9.9': [{}]}},
            '0.3.2.dev5+gabc1234',
        )
        warmed = []
        monkeypatch.setattr(au.pip_update, 'warm', lambda v: warmed.append(v))
        await au.run_cycle()
        assert au.snapshot()['state'] == au.IDLE
        assert au.snapshot()['version'] is None
        assert warmed == []

    async def test_a_dev_build_is_not_in_app_update(self, monkeypatch):
        monkeypatch.setenv('VIBE_SELLER_AUTO_UPDATE', '1')
        monkeypatch.setattr(au, 'get_version', lambda: '0.0.0+dev')
        assert au.in_app_update() is False

    async def test_a_dev_watch_loop_exits_instead_of_polling(self, monkeypatch):
        """The loop is what burns a request every six hours; a dev build
        must not start one at all."""
        monkeypatch.setattr(au, 'get_version', lambda: '0.3.2.dev5+gabc')
        ran = []
        monkeypatch.setattr(au, 'run_cycle', lambda: ran.append(1))
        # Returns at once, having done nothing: no first check, no
        # request, no six-hourly poll for the life of the process.
        await asyncio.wait_for(au.watch(), 2.0)
        assert ran == []


# ── the ready clock and the popup ────────────────────────────────────


class TestReadyClock:
    async def test_the_pip_path_starts_a_quiet_clock(
        self, windows, monkeypatch
    ):
        _pip_leg(monkeypatch, windows)
        _record_events(monkeypatch)
        await au.run_cycle()
        first = au.snapshot()
        assert first['ready_since']
        assert first['popup_due'] is False
        # Six hours later the same release is the same wait, not a new one.
        await au.run_cycle()
        assert au.snapshot()['ready_since'] == first['ready_since']

    async def test_the_installer_path_asks_at_once(self, windows, monkeypatch):
        _pip_leg(monkeypatch, windows, release_fp=_OTHER)
        _record_events(monkeypatch)
        await au.run_cycle()
        assert au.snapshot()['popup_due'] is True

    async def test_no_update_offer_clears_the_clock(
        self, tmp_path, monkeypatch
    ):
        _pypi(
            tmp_path,
            monkeypatch,
            {'info': {'version': '0.8.0'}, 'releases': {}},
            '0.8.0',
        )
        au._mark_ready('0.9.0', PIP)
        assert au.snapshot()['ready_since']
        await au._cycle_unix()
        assert au.snapshot()['ready_since'] is None
        assert au.snapshot()['popup_due'] is False


# ── the auto-apply gate ──────────────────────────────────────────────


class TestIdleTick:
    """The auto-apply gate, one decision at a time.

    `idle_tick` is the whole policy in one callable, so these tests ask
    it a question and read the answer — no background task, no clock, and
    nothing two tests can leave behind for each other.
    """

    @pytest.fixture(autouse=True)
    def _enabled(self, monkeypatch):
        monkeypatch.setenv('VIBE_SELLER_AUTO_UPDATE', '1')
        monkeypatch.setattr(au, 'supported', lambda: True)

        # `evaluate` stays the real policy; only its two questions about
        # the world outside this process are answered here.

        async def no_tasks():
            return 0

        monkeypatch.setattr(au.update_idle, 'busy_task_count', no_tasks)
        monkeypatch.setattr(
            au.update_idle, 'schedule_due_within', lambda s, now=None: None
        )

    def _ready(self, path=PIP):
        au._update(
            state=au.READY, version='0.9.0', path=path, ready_since=time.time()
        )
        return Queue(), Agents()

    async def test_an_idle_machine_applies_itself(self, monkeypatch):
        queue, agents = self._ready()
        seen = []
        monkeypatch.setattr(au, 'apply_restart', _recorder(seen))
        assert await au.idle_tick(queue, agents) == 'applied'
        assert seen[0]['trigger'] == pip_update.TRIGGER_AUTO_IDLE
        assert seen[0]['interrupted_tasks'] == 0

    async def test_the_installer_path_never_applies_itself(self, monkeypatch):
        """#244's contract, unchanged: only a click runs an installer."""
        queue, agents = self._ready(path=INSTALLER)
        seen = []
        monkeypatch.setattr(au, 'apply_restart', _recorder(seen))
        assert await au.idle_tick(queue, agents) == 'not ready'
        assert seen == []
        assert queue.log == [], 'paused dispatch for nothing'

    async def test_a_busy_machine_is_left_alone(self, monkeypatch):
        queue, _ = self._ready()
        seen = []
        monkeypatch.setattr(au, 'apply_restart', _recorder(seen))
        answer = await au.idle_tick(queue, Agents(sequence=[1]))
        assert answer.startswith('busy:')
        assert 'task agent' in answer
        assert seen == []
        assert queue.log == []

    async def test_a_queued_task_blocks_the_apply(self, monkeypatch):
        queue, agents = self._ready()
        seen = []

        async def one_queued():
            return 1

        monkeypatch.setattr(au.update_idle, 'busy_task_count', one_queued)
        monkeypatch.setattr(au, 'apply_restart', _recorder(seen))
        assert 'queued' in await au.idle_tick(queue, agents)
        assert seen == []

    async def test_a_schedule_about_to_fire_blocks_the_apply(self, monkeypatch):
        queue, agents = self._ready()
        seen = []
        monkeypatch.setattr(
            au.update_idle,
            'schedule_due_within',
            lambda s, now=None: 'schedule_9',
        )
        monkeypatch.setattr(au, 'apply_restart', _recorder(seen))
        assert 'schedule_9' in await au.idle_tick(queue, agents)
        assert seen == []

    async def test_dispatch_is_paused_before_the_last_look(self, monkeypatch):
        """The race this closes: "is anything running?" and the restart are
        separate steps, so dispatch has to be stopped BEFORE the answer is
        trusted — otherwise a task can start in the gap and be killed."""
        queue, _ = self._ready()
        # Idle for the policy check; live by the time the gate asks.
        agents = Agents(sequence=[0, 1])
        seen = []
        monkeypatch.setattr(au, 'apply_restart', _recorder(seen))
        assert 'an agent started' in await au.idle_tick(queue, agents)
        assert seen == [], 'applied while an agent had started'
        assert queue.log == ['pause', 'resume']
        assert agents.calls == 2

    async def test_the_pause_happens_even_when_the_policy_says_go_ahead(
        self, monkeypatch
    ):
        """Ordering, stated as an assertion: the gate's answer is only
        trustworthy because dispatch was already stopped when it was read."""
        queue, _ = self._ready()
        log = []

        class Strict(Queue):
            def pause_dispatch(self):
                log.append('pause')
                super().pause_dispatch()

        class Watching(Agents):
            def live_agent_count(self):
                log.append(f'live:{self.calls}')
                return super().live_agent_count()

        monkeypatch.setattr(au, 'apply_restart', _recorder([]))
        await au.idle_tick(Strict(), Watching())
        assert log == ['live:0', 'pause', 'live:1'], log

    async def test_a_held_lock_is_a_retry_not_a_failure(self, monkeypatch):
        queue, agents = self._ready()

        async def refused(**kw):
            raise pip_update.ApplyInProgress('another update is applying')

        monkeypatch.setattr(au, 'apply_restart', refused)
        assert await au.idle_tick(queue, agents) == 'refused'
        assert queue.paused is False, 'left dispatch paused after refusing'
        assert au.snapshot()['state'] == au.READY

    async def test_a_failed_apply_retries_on_the_next_tick(self, monkeypatch):
        queue, agents = self._ready()
        attempts = []

        async def fails(**kw):
            attempts.append(kw)
            raise RuntimeError('the disk is full')

        monkeypatch.setattr(au, 'apply_restart', fails)
        with pytest.raises(RuntimeError):
            await au.idle_tick(queue, agents)
        assert queue.paused is False, 'unpaused, so the next tick can work'
        with pytest.raises(RuntimeError):
            await au.idle_tick(queue, agents)
        assert len(attempts) == 2

    async def test_a_ready_update_that_never_arrives_leaves_the_state_alone(
        self, monkeypatch
    ):
        au._update(state=au.IDLE, version=None, path=None)
        queue, agents = Queue(), Agents()
        seen = []
        monkeypatch.setattr(au, 'apply_restart', _recorder(seen))
        assert await au.idle_tick(queue, agents) == 'not ready'
        assert seen == []


def _recorder(sink):
    async def fake(**kw):
        sink.append(kw)
        return {'state': au.RESTARTING}

    return fake


class TestApplyRestart:
    async def test_the_installer_is_launched_silently(
        self, windows, monkeypatch
    ):
        _make_local_stage()
        au._update(state=au.READY, version='0.2.0', path=INSTALLER)
        events = _record_events(monkeypatch)
        with patch.object(wu.subprocess, 'Popen') as popen:
            out = await au.apply_restart(
                interrupted_tasks=0, trigger=pip_update.TRIGGER_MANUAL
            )
        args = popen.call_args[0][0]
        assert args[1:4] == ['/SILENT', '/SUPPRESSMSGBOXES', '/NORESTART']
        # The account of the unattended run lands beside the staged exe.
        assert args[4].startswith('/LOG=')
        assert 'updates' in args[4]
        assert out == {
            'state': au.RESTARTING,
            'version': '0.2.0',
            'path': INSTALLER,
        }
        assert au.snapshot()['state'] == au.RESTARTING
        evs = [
            e for e in events if e[0] == TelemetryEvent.UPDATE_RESTART_REQUESTED
        ]
        assert evs[0][1]['interrupted_tasks'] == '0'
        assert evs[0][1]['path'] == INSTALLER
        assert evs[0][1]['trigger'] == pip_update.TRIGGER_MANUAL

    async def test_a_missing_stage_fails_closed(self, windows, monkeypatch):
        """The bytes are re-verified before launching something that stops
        the server: a ready stage that went bad since must not run."""
        au._update(state=au.READY, version='9.9.9', path=INSTALLER)
        _record_events(monkeypatch)
        with pytest.raises(RuntimeError, match='staged installer is missing'):
            await au.apply_restart(interrupted_tasks=0)
        assert au.snapshot()['state'] == au.ERROR

    async def test_the_pip_path_hands_the_machine_to_the_helper(
        self, monkeypatch, tmp_path
    ):
        """Both platforms, one mechanism: a detached helper that survives
        the install it replaces."""
        monkeypatch.setattr(au, 'get_version', lambda: '0.8.0')
        monkeypatch.setattr(au.sys, 'platform', 'linux')
        spawned = []
        monkeypatch.setattr(
            au.apply_launch,
            'apply',
            lambda v, trigger, path: (
                spawned.append((v, trigger, path)) or FakeProc()
            ),
        )
        au._update(state=au.READY, version='0.9.0', path=PIP, ready_since=1.0)
        _record_events(monkeypatch)
        out = await au.apply_restart(
            interrupted_tasks=0, trigger=pip_update.TRIGGER_AUTO_IDLE
        )
        assert spawned == [('0.9.0', pip_update.TRIGGER_AUTO_IDLE, PIP)]
        assert out['path'] == PIP
        assert au.snapshot()['state'] == au.RESTARTING
        await au._apply_task

    async def test_a_helper_that_fails_while_the_server_lives_says_so(
        self, monkeypatch
    ):
        """The machine did not move. Without this the UI would sit on
        "restarting" until the browser gave up."""
        monkeypatch.setattr(au, 'get_version', lambda: '0.8.0')
        monkeypatch.setattr(au.sys, 'platform', 'linux')
        monkeypatch.setattr(au.apply_launch, 'apply', lambda *a: FakeProc())
        failed = {
            'status': pip_update.ROLLED_BACK,
            'stage': 'health',
            'error': 'the server never reported 0.9.0',
            'from': '0.8.0',
            'to': '0.9.0',
            'path': PIP,
            'reported': False,
        }
        where = wu.updates_root() / 'last.json'
        where.parent.mkdir(parents=True, exist_ok=True)
        where.write_text(json.dumps(failed), encoding='utf-8')
        monkeypatch.setattr(au.pip_update, 'read_record', lambda: dict(failed))
        # The watcher ignores a record older than the apply it is
        # watching, so say this one arrived after the handoff.
        monkeypatch.setattr(au.pip_update, 'record_mtime', lambda: 1.0)
        monkeypatch.setattr(au.asyncio, 'to_thread', _inline_thread)
        # A record only counts if it appeared AFTER the apply started, so
        # hand the watcher a clock that advances on every read.
        clock = iter(range(100))
        monkeypatch.setattr(
            au.pip_update, 'record_mtime', lambda: float(next(clock, 1000))
        )
        events = _record_events(monkeypatch)
        au._update(state=au.READY, version='0.9.0', path=PIP, ready_since=1.0)
        await au.apply_restart(interrupted_tasks=0)
        await au._apply_task
        snap = au.snapshot()
        assert snap['state'] == au.ERROR
        assert 'reverted to v0.8.0' in snap['error']
        bad = [e for e in events if e[0] == TelemetryEvent.UPDATE_APPLY_FAILED]
        assert bad and bad[0][1]['path'] == PIP
        assert bad[0][1]['stage'] == 'health'

    async def test_the_restart_event_bucket_for_two_plus(
        self, windows, monkeypatch
    ):
        _make_local_stage()
        au._update(state=au.READY, version='0.2.0', path=INSTALLER)
        events = _record_events(monkeypatch)
        with patch.object(wu.subprocess, 'Popen'):
            await au.apply_restart(
                interrupted_tasks=5, trigger=pip_update.TRIGGER_MANUAL
            )
        evs = [
            e for e in events if e[0] == TelemetryEvent.UPDATE_RESTART_REQUESTED
        ]
        assert evs[0][1]['interrupted_tasks'] == '2+'

    async def test_nothing_is_applied_that_is_not_staged(self, windows):
        # The router refuses a restart unless the state says `ready`, but
        # the state machine answers for itself too: an apply with no
        # target is a RuntimeError the router turns into a 500, never a
        # crash that leaves RESTARTING stuck on the screen.
        au._update(state=au.READY, version=None, path=PIP)
        with pytest.raises(RuntimeError, match='no update is staged'):
            await au.apply_restart(
                interrupted_tasks=0, trigger=pip_update.TRIGGER_MANUAL
            )
        # Refusing to start changed nothing: the ready target and its
        # clock are still there to apply on the next click.
        assert au.snapshot()['state'] == au.READY


async def _inline_thread(fn, *a, **kw):
    return fn(*a, **kw)


class TestManualTrigger:
    def test_a_click_within_the_day_is_just_a_click(self):
        au._update(
            state=au.READY,
            version='0.9.0',
            path=PIP,
            ready_since=time.time(),
        )
        assert au.manual_trigger() == pip_update.TRIGGER_MANUAL

    def test_a_click_the_timeout_asked_for_is_labelled_as_such(self):
        """It is the only record of "this machine was never idle", which
        is what tells us whether 24 h is the right number."""
        au._update(
            state=au.READY,
            version='0.9.0',
            path=PIP,
            ready_since=time.time()
            - au.update_idle.READY_POPUP_AFTER_SECONDS
            - 1,
        )
        assert au.manual_trigger() == pip_update.TRIGGER_MANUAL_AFTER_24H

    def test_the_installer_path_always_asks(self):
        au._update(
            state=au.READY,
            version='0.9.0',
            path=INSTALLER,
            ready_since=time.time() - 10**6,
        )
        assert au.manual_trigger() == pip_update.TRIGGER_MANUAL


# ── boot: what the last apply left behind ────────────────────────────


class TestBootRecord:
    def test_a_rolled_back_update_is_reported_once(self, monkeypatch):
        doc = {
            'status': pip_update.ROLLED_BACK,
            'stage': 'health',
            'error': 'the server never reported 0.9.0',
            'path': PIP,
            'reported': False,
        }
        events = _record_events(monkeypatch)
        monkeypatch.setattr(au.pip_update, 'read_record', lambda: doc)
        marked = []
        monkeypatch.setattr(
            au.pip_update, 'mark_record_reported', lambda: marked.append(1)
        )
        assert au.load_last_update()['status'] == pip_update.ROLLED_BACK
        bad = [e for e in events if e[0] == TelemetryEvent.UPDATE_APPLY_FAILED]
        assert bad and bad[0][1]['stage'] == 'health'
        assert marked == [1]

    def test_a_successful_update_is_not_reported_as_a_failure(
        self, monkeypatch
    ):
        events = _record_events(monkeypatch)
        monkeypatch.setattr(
            au.pip_update,
            'read_record',
            lambda: {'status': 'ok', 'path': PIP, 'reported': False},
        )
        au.load_last_update()
        assert not [
            e for e in events if e[0] == TelemetryEvent.UPDATE_APPLY_FAILED
        ]

    def test_the_record_reaches_the_ui(self, monkeypatch):
        monkeypatch.setattr(
            au.pip_update,
            'read_record',
            lambda: {'status': 'rolled_back', 'from': '0.8.0', 'to': '0.9.0'},
        )
        au.load_last_update()
        assert au.snapshot()['last_update']['to'] == '0.9.0'

    def test_a_cold_machine_reports_the_same_record_again(self, monkeypatch):
        """`reset_for_tests` is a cold machine: a process that has never
        seen this apply. Keeping the once-per-apply key across it would
        make every later test that loads an identically keyed record
        skip its refusal and its report — silently, by test order."""
        doc = {
            'status': pip_update.ROLLED_BACK,
            'stage': 'health',
            'path': PIP,
            'from': '0.8.0',
            'to': '0.9.0',
            'at': '2026-01-01T00:00:00Z',
            'reported': False,
        }
        events = _record_events(monkeypatch)
        monkeypatch.setattr(au.pip_update, 'read_record', lambda: doc)
        monkeypatch.setattr(au.pip_update, 'mark_record_reported', lambda: None)
        au.load_last_update()
        au.reset_for_tests()
        au.load_last_update()
        bad = [e for e in events if e[0] == TelemetryEvent.UPDATE_APPLY_FAILED]
        assert len(bad) == 2

    def test_no_record_is_not_an_error(self, monkeypatch):
        monkeypatch.setattr(au.pip_update, 'read_record', lambda: None)
        assert au.load_last_update() is None
        assert au.snapshot()['last_update'] is None


# ── the prefetch must never be the thing that updates ────────────────


class TestPrefetchSafety:
    """Regression for the bug #244 found live: ``uv tool run --python
    <the tool venv's own python>`` installs into that venv — the tool
    moved to the target and the restart watcher brought the server up on
    a new version with nobody asking. The construction now lives in
    app/pip_update.py; what is pinned here is that no path through the
    state machine reaches the install either."""

    async def test_cycles_never_restart_the_server(self, tmp_path, monkeypatch):
        _pypi(
            tmp_path,
            monkeypatch,
            {'info': {'version': '0.9.0'}, 'releases': {'0.9.0': [{}]}},
            '0.8.0',
        )
        monkeypatch.setattr(au.pip_update, 'warm', lambda v: None)
        monkeypatch.setattr(
            au.pip_update, 'pre_install_browser', lambda v: None
        )
        monkeypatch.setattr(au.pip_update, 'keep_rollback_warm', lambda: None)
        restarted = []
        monkeypatch.setattr(
            upgrade_restart,
            'request_restart',
            lambda version=None: restarted.append(version),
        )
        for _ in range(3):
            await au.run_cycle()
        assert au.snapshot()['state'] == au.READY
        assert restarted == []

    async def test_a_ready_pip_update_never_applies_without_a_tick(
        self, tmp_path, monkeypatch
    ):
        """``idle_watch`` is the only applier, and it is not running in
        this test."""
        _pypi(
            tmp_path,
            monkeypatch,
            {'info': {'version': '0.9.0'}, 'releases': {'0.9.0': [{}]}},
            '0.8.0',
        )
        monkeypatch.setattr(au.pip_update, 'warm', lambda v: None)
        monkeypatch.setattr(
            au.pip_update, 'pre_install_browser', lambda v: None
        )
        monkeypatch.setattr(au.pip_update, 'keep_rollback_warm', lambda: None)
        applied = []
        monkeypatch.setattr(
            au, 'apply_restart', lambda **kw: applied.append(kw)
        )
        await au.run_cycle()
        await asyncio.sleep(0)
        assert applied == []


class TestSingleFlight:
    """A second apply — an admin's click during an auto-apply, or another
    server on the same ``VIBE_HOME`` — must be refused, not queued behind
    and not run alongside."""

    async def test_a_held_lock_refuses_without_leaving_restarting(
        self, windows, monkeypatch
    ):
        monkeypatch.setattr(au.sys, 'platform', 'linux')
        monkeypatch.setattr(
            au.apply_launch,
            'apply',
            lambda *a: (_ for _ in ()).throw(
                pip_update.ApplyInProgress('another update is applying')
            ),
        )
        queue = Queue()
        au._update(state=au.READY, version='0.9.0', path=PIP, ready_since=1.0)
        with pytest.raises(pip_update.ApplyInProgress):
            await au.apply_restart(
                interrupted_tasks=0,
                trigger=pip_update.TRIGGER_MANUAL,
            )
        # Back to waiting, clock intact: nothing failed, something else
        # is busy, and the next tick will look again.
        snap = au.snapshot()
        assert snap['state'] == au.READY
        assert snap['ready_since'] == 1.0
        assert queue.paused is False

    def test_the_probe_reports_the_holder(self, tmp_path, monkeypatch):
        monkeypatch.setattr(au.pip_update, 'updates_dir', lambda: tmp_path)
        assert au.pip_update.apply_in_progress() is None
        import os  # noqa: PLC0415, E402

        fd = os.open(str(tmp_path / 'apply.lock'), os.O_RDWR | os.O_CREAT)
        try:
            au.pip_update._lock(fd, non_blocking=True)
            held = au.pip_update.apply_in_progress()
            assert held and 'another update is being applied' in held
        finally:
            os.close(fd)
        assert au.pip_update.apply_in_progress() is None


class TestSnapshotShape:
    def test_the_fields_the_modal_reads(self):
        snap = au.snapshot()
        assert {
            'state',
            'version',
            'path',
            'auto_apply',
            'popup_due',
            'ready_since',
            'last_update',
            'downloaded_bytes',
            'total_bytes',
            'error',
        } <= set(snap)

    def test_idle_implies_no_popup_and_no_auto_apply(self):
        snap = au.snapshot()
        assert snap['state'] == au.IDLE
        assert snap['auto_apply'] is False
        assert snap['popup_due'] is False

    async def test_a_ready_installer_update_reports_both(
        self, windows, monkeypatch
    ):
        _pip_leg(monkeypatch, windows, release_fp=_OTHER)
        _record_events(monkeypatch)
        await au.run_cycle()
        snap = au.snapshot()
        assert snap['auto_apply'] is False
        assert snap['popup_due'] is True
