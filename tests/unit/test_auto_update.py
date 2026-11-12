"""Tests for app/auto_update — the in-app update state machine.

Platform branching is exercised by faking ``sys.platform`` (the repo's
established pattern in test_windows_update*), with the process/HTTP
boundaries patched where a real subprocess would run:
``subprocess.Popen``/``subprocess.run``. Everything else — staging,
verification, state transitions, events — runs for real.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import app.auto_update as au
from app.telemetry_events import TelemetryEvent
import app.windows_update as wu

pytestmark = pytest.mark.unit

_BLOB = b'installer-bytes' * 100


@pytest.fixture(autouse=True)
def _reset_state(tmp_path, monkeypatch):
    au.reset_for_tests()
    # Never touch the machine's real uv-receipt / unins000 detection,
    # and keep staging inside the test tmp dir.
    monkeypatch.setattr(
        au.windows_update, 'updates_root', lambda: tmp_path / 'updates'
    )
    yield
    au.reset_for_tests()


def _record_events(monkeypatch) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []

    def fake_send(event, properties=None):
        events.append((event, dict(properties or {})))

    monkeypatch.setattr(au.telemetry, 'send', fake_send)
    return events


def _make_local_stage(version='0.2.0', blob=_BLOB):
    import hashlib  # noqa: PLC0415 — test-local

    src = Path(wu.updates_root()) / 'src.exe'
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(blob)
    offer = {
        'version': version,
        'url': str(src),
        'size': len(blob),
        'digest': 'sha256:' + hashlib.sha256(blob).hexdigest(),
    }
    wu.stage_installer(offer)
    return offer


class TestSupported:
    def test_dev_channel_never(self, monkeypatch):
        monkeypatch.setattr(au, 'get_version', lambda: '0.2.1.dev3+gabc123')
        assert au.supported() is False

    def test_windows_bundle(self, monkeypatch, tmp_path):
        prefix = tmp_path / 'app' / '.venv'
        prefix.mkdir(parents=True)
        (tmp_path / 'app' / 'unins000.exe').write_bytes(b'')
        monkeypatch.setattr(au.sys, 'platform', 'win32')
        monkeypatch.setattr(au.sys, 'prefix', str(prefix))
        monkeypatch.setattr(au, 'get_version', lambda: '0.1.0')
        assert au.supported() is True

    def test_windows_non_bundle(self, monkeypatch, tmp_path):
        prefix = tmp_path / 'other' / '.venv'
        prefix.mkdir(parents=True)
        monkeypatch.setattr(au.sys, 'platform', 'win32')
        monkeypatch.setattr(au.sys, 'prefix', str(prefix))
        monkeypatch.setattr(au, 'get_version', lambda: '0.1.0')
        assert au.supported() is False

    def test_uv_tool_with_uv_on_path(self, monkeypatch, tmp_path):
        monkeypatch.setattr(au.sys, 'platform', 'linux')
        monkeypatch.setattr(au.sys, 'prefix', str(tmp_path))
        (tmp_path / 'uv-receipt.toml').write_text('[tool]\n', encoding='utf-8')
        monkeypatch.setattr(au, 'get_version', lambda: '0.1.0')
        monkeypatch.setattr(au.shutil, 'which', lambda name: '/usr/bin/uv')
        assert au.supported() is True

    def test_pip_or_source_install_not_supported(self, monkeypatch, tmp_path):
        monkeypatch.setattr(au.sys, 'platform', 'linux')
        monkeypatch.setattr(au.sys, 'prefix', str(tmp_path))
        monkeypatch.setattr(au, 'get_version', lambda: '0.1.0')
        monkeypatch.setattr(au.shutil, 'which', lambda name: '/usr/bin/uv')
        assert au.supported() is False

    def test_no_uv_binary_not_supported(self, monkeypatch, tmp_path):
        monkeypatch.setattr(au.sys, 'platform', 'linux')
        monkeypatch.setattr(au.sys, 'prefix', str(tmp_path))
        (tmp_path / 'uv-receipt.toml').write_text('[tool]\n', encoding='utf-8')
        monkeypatch.setattr(au, 'get_version', lambda: '0.1.0')
        monkeypatch.setattr(au.shutil, 'which', lambda name: None)
        assert au.supported() is False


class TestEnabled:
    def test_off_under_pytest_by_default(self, monkeypatch):
        monkeypatch.delenv('VIBE_SELLER_AUTO_UPDATE', raising=False)
        assert au.enabled() is False

    def test_explicit_on_beats_pytest(self, monkeypatch):
        monkeypatch.setenv('VIBE_SELLER_AUTO_UPDATE', '1')
        assert au.enabled() is True

    def test_explicit_off(self, monkeypatch):
        monkeypatch.setenv('VIBE_SELLER_AUTO_UPDATE', '0')
        assert au.enabled() is False


class TestUnixCycle:
    async def test_stable_never_stages_a_nightly(self, monkeypatch, tmp_path):
        pypi = {
            'info': {'version': '0.9.0'},
            'releases': {
                '0.8.0': [{'yanked': False}],
                '0.9.0': [{'yanked': False}],
                '0.10.0.dev202611010000': [{'yanked': False}],
            },
        }
        src = tmp_path / 'pypi.json'
        src.write_text(json.dumps(pypi), encoding='utf-8')
        monkeypatch.setenv('VIBE_SELLER_PYPI_JSON_URL', str(src))
        monkeypatch.setattr(au, 'get_version', lambda: '0.8.0')
        ran = []

        def fake_run(cmd, **_kw):
            ran.append(cmd)
            return type(
                'R', (), {'returncode': 0, 'stdout': '', 'stderr': ''}
            )()

        monkeypatch.setattr(au.subprocess, 'run', fake_run)
        events = _record_events(monkeypatch)
        await au._cycle_unix()
        assert au.snapshot()['state'] == au.READY
        assert au.snapshot()['version'] == '0.9.0'
        assert ran and ran[0] == [
            'uv',
            'tool',
            'run',
            '--from',
            'vibe-seller==0.9.0',
            '--python',
            au.sys.executable,
            'vibe-seller',
            '--version',
        ]
        evs = [e for e in events if e[0] == TelemetryEvent.UPDATE_DOWNLOADED]
        assert evs and evs[0][1]['outcome'] == 'ok'
        assert evs[0][1]['to_version'] == '0.9.0'

    async def test_nightly_current_targets_highest_newer(
        self, monkeypatch, tmp_path
    ):
        pypi = {
            'info': {'version': '0.9.0'},
            'releases': {
                '0.9.0': [{'yanked': False}],
                '0.10.0.dev202611010000': [{'yanked': False}],
            },
        }
        src = tmp_path / 'pypi.json'
        src.write_text(json.dumps(pypi), encoding='utf-8')
        monkeypatch.setenv('VIBE_SELLER_PYPI_JSON_URL', str(src))
        monkeypatch.setattr(au, 'get_version', lambda: '0.10.0.dev202610100000')
        ran: list = []
        monkeypatch.setattr(
            au.subprocess,
            'run',
            lambda cmd, **kw: ran.append(cmd)
            or type('R', (), {'returncode': 0, 'stdout': '', 'stderr': ''})(),
        )
        _record_events(monkeypatch)
        await au._cycle_unix()
        assert au.snapshot()['version'] == '0.10.0.dev202611010000'
        assert ran[0][4] == 'vibe-seller==0.10.0.dev202611010000'

    async def test_prefetch_failure_sets_error_and_event(
        self, monkeypatch, tmp_path
    ):
        pypi = {'info': {'version': '0.9.0'}, 'releases': {}}
        src = tmp_path / 'pypi.json'
        src.write_text(json.dumps(pypi), encoding='utf-8')
        monkeypatch.setenv('VIBE_SELLER_PYPI_JSON_URL', str(src))
        monkeypatch.setattr(au, 'get_version', lambda: '0.8.0')

        def bad_run(cmd, **_kw):
            return type(
                'R', (), {'returncode': 1, 'stdout': '', 'stderr': 'no network'}
            )()

        monkeypatch.setattr(au.subprocess, 'run', bad_run)
        events = _record_events(monkeypatch)
        await au._cycle_unix()
        snap = au.snapshot()
        assert snap['state'] == au.ERROR
        assert 'no network' in snap['error']
        evs = [e for e in events if e[0] == TelemetryEvent.UPDATE_DOWNLOADED]
        assert evs[0][1]['outcome'] == 'failed'
        assert evs[0][1]['stage'] == 'prefetch'

    async def test_up_to_date_stays_idle(self, monkeypatch, tmp_path):
        pypi = {'info': {'version': '0.8.0'}, 'releases': {}}
        src = tmp_path / 'pypi.json'
        src.write_text(json.dumps(pypi), encoding='utf-8')
        monkeypatch.setenv('VIBE_SELLER_PYPI_JSON_URL', str(src))
        monkeypatch.setattr(au, 'get_version', lambda: '0.8.0')
        await au._cycle_unix()
        assert au.snapshot()['state'] == au.IDLE


class TestWindowsCycle:
    async def _cycle(self, monkeypatch):
        monkeypatch.setattr(au.sys, 'platform', 'win32')
        await au.run_cycle()

    async def test_download_then_ready(self, monkeypatch, tmp_path):
        src = tmp_path / 'setup.exe'
        src.write_bytes(_BLOB)
        offer = {
            'version': '0.2.0',
            'url': str(src),
            'size': len(_BLOB),
            'digest': None,
        }
        monkeypatch.setattr(wu, 'check_for_update', lambda: offer)
        _record_events(monkeypatch)
        await self._cycle(monkeypatch)
        snap = au.snapshot()
        assert snap['state'] == au.READY
        assert snap['version'] == '0.2.0'

    async def test_boot_with_verified_stage_is_ready_without_download(
        self, monkeypatch
    ):
        _make_local_stage()
        monkeypatch.setattr(au, 'get_version', lambda: '0.1.0')
        monkeypatch.setattr(
            wu,
            'check_for_update',
            lambda: (_ for _ in ()).throw(RuntimeError('offline')),
        )
        await self._cycle(monkeypatch)
        snap = au.snapshot()
        assert snap['state'] == au.READY
        assert snap['version'] == '0.2.0'

    async def test_error_state_when_source_missing(self, monkeypatch):
        offer = {
            'version': '0.2.0',
            'url': '/nonexistent/VibeSeller-Setup.exe',
            'size': 10,
            'digest': None,
        }
        monkeypatch.setattr(wu, 'check_for_update', lambda: offer)
        events = _record_events(monkeypatch)
        monkeypatch.setattr(au.time, 'sleep', lambda *_: None)
        await self._cycle(monkeypatch)
        assert au.snapshot()['state'] == au.ERROR
        evs = [e for e in events if e[0] == TelemetryEvent.UPDATE_DOWNLOADED]
        assert evs and evs[0][1]['outcome'] == 'failed'
        assert evs[0][1]['stage'] in ('download', 'verify')

    async def test_event_send_failure_never_breaks_the_cycle(
        self, monkeypatch, tmp_path
    ):
        src = tmp_path / 'setup.exe'
        src.write_bytes(_BLOB)
        offer = {
            'version': '0.2.0',
            'url': str(src),
            'size': len(_BLOB),
            'digest': None,
        }
        monkeypatch.setattr(wu, 'check_for_update', lambda: offer)

        def boom(*_a, **_k):
            raise RuntimeError('events are down')

        monkeypatch.setattr(au.telemetry, 'send', boom)
        await self._cycle(monkeypatch)
        assert au.snapshot()['state'] == au.READY


class TestApplyRestart:
    async def test_windows_launches_installer_with_silent(self, monkeypatch):
        _make_local_stage()
        monkeypatch.setattr(au.sys, 'platform', 'win32')
        au._update(state=au.READY, version='0.2.0')
        events = _record_events(monkeypatch)
        with patch.object(wu.subprocess, 'Popen') as popen:
            out = await au.apply_restart(interrupted_tasks=0)
        args = popen.call_args[0][0]
        assert args[1:] == ['/SILENT', '/SUPPRESSMSGBOXES', '/NORESTART']
        assert out == {'state': au.RESTARTING, 'version': '0.2.0'}
        assert au.snapshot()['state'] == au.RESTARTING
        evs = [
            e for e in events if e[0] == TelemetryEvent.UPDATE_RESTART_REQUESTED
        ]
        assert evs[0][1]['interrupted_tasks'] == '0'
        assert evs[0][1]['to_version'] == '0.2.0'

    async def test_windows_missing_stage_reports_error(self, monkeypatch):
        monkeypatch.setattr(au.sys, 'platform', 'win32')
        au._update(state=au.READY, version='9.9.9')
        _record_events(monkeypatch)
        with pytest.raises(RuntimeError):
            await au.apply_restart(interrupted_tasks=0)
        assert au.snapshot()['state'] == au.ERROR

    async def test_unix_runs_upgrade_then_requests_restart(
        self, monkeypatch, tmp_path
    ):
        # The whole path runs for real except two boundaries: the
        # upgrade child process (Popen) and the SIGTERM-ing
        # request_restart.
        monkeypatch.setattr(au, 'get_version', lambda: '0.8.0')
        monkeypatch.setattr(au, 'VIBE_SELLER_DIR', tmp_path)
        spawned: dict = {}

        class _Proc:
            returncode = None

            def poll(self):
                return None

        def fake_popen(argv, **kw):
            spawned['argv'] = argv
            spawned['kw'] = kw
            return _Proc()

        monkeypatch.setattr(au.subprocess, 'Popen', fake_popen)
        restarts: list[str] = []
        monkeypatch.setattr(
            au.upgrade_restart,
            'request_restart',
            lambda: restarts.append('restart'),
        )
        seen = {'n': 0}

        def installed_version():
            seen['n'] += 1
            # First poll: still the old version; then the upgrade lands.
            return '0.9.0' if seen['n'] > 1 else '0.8.0'

        monkeypatch.setattr(
            au.upgrade_restart, 'installed_version', installed_version
        )
        au._update(state=au.READY, version='0.9.0')
        await au.apply_restart(interrupted_tasks=2)
        await au._apply_task
        # What `vibe-seller upgrade` is: the real command line,
        # detached in its own session, logging under updates/.
        assert spawned['argv'][-1] == 'upgrade'
        assert spawned['kw']['start_new_session'] is True
        assert restarts == ['restart']
        assert au.snapshot()['state'] == au.RESTARTING

    async def test_unix_upgrade_failure_keeps_serving_and_sends_event(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(au, 'get_version', lambda: '0.8.0')
        monkeypatch.setattr(au, 'VIBE_SELLER_DIR', tmp_path)

        class _Proc:
            returncode = 1

            def poll(self):
                return 1

        monkeypatch.setattr(au.subprocess, 'Popen', lambda argv, **kw: _Proc())
        monkeypatch.setattr(
            au.upgrade_restart, 'installed_version', lambda: '0.8.0'
        )
        restarts: list = []
        monkeypatch.setattr(
            au.upgrade_restart, 'request_restart', lambda: restarts.append(1)
        )
        events = _record_events(monkeypatch)
        au._update(state=au.READY, version='0.9.0')
        await au.apply_restart(interrupted_tasks=1)
        await au._apply_task
        assert restarts == []
        snap = au.snapshot()
        assert snap['state'] == au.ERROR
        assert 'exited 1' in snap['error']
        assert any(e[0] == TelemetryEvent.UPDATE_APPLY_FAILED for e in events)

    async def test_restart_event_bucket_for_two_plus_tasks(self, monkeypatch):
        _make_local_stage()
        monkeypatch.setattr(au.sys, 'platform', 'win32')
        au._update(state=au.READY, version='0.2.0')
        events = _record_events(monkeypatch)
        with patch.object(wu.subprocess, 'Popen'):
            await au.apply_restart(interrupted_tasks=5)
        evs = [
            e for e in events if e[0] == TelemetryEvent.UPDATE_RESTART_REQUESTED
        ]
        assert evs[0][1]['interrupted_tasks'] == '2+'
