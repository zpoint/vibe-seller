"""A release the machine found and could not download sends the admin to
the manual route — and stops doing so the moment it no longer applies.

The cycle itself is not what is under test: it retries exactly as
before. What is pinned is the memory beside it (``app/update_manual.py``)
and what ``auto_update.snapshot`` makes of it, through every way a real
machine moves:

- a download fails → ``error``, recorded on disk, ``manual_due``;
- it fails again → the same record, one more attempt;
- the next CHECK fails (the usual sequel on a network that cannot reach
  the release source) → ``idle``, and the guide still stands;
- the server restarts → the guide is still there (the machine in the field
  that started this was switched off every evening);
- a retry is downloading → no guide, the chip shows the download;
- a retry reaches ``ready`` → the record is gone, the in-app flow is back;
- the admin updated by hand (or the machine is already on that release)
  → nothing to say, and the stale file is removed at boot.

The network/process boundaries are patched where they are called
(``wu.check_for_update``, ``pip_update.warm``); staging, the state
machine and the file are real.
"""

import json
import logging
from pathlib import Path

import pytest

from app import pip_update, update_manual
import app.auto_update as au
import app.windows_update as wu

pytestmark = pytest.mark.unit

_BLOB = b'installer-bytes' * 100


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
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
    monkeypatch.setattr(au.update_events.telemetry, 'send', lambda *a, **k: 0)
    yield
    au.reset_for_tests()


@pytest.fixture(name='windows')
def _windows(monkeypatch, tmp_path):
    """A native-Windows installer bundle on 0.1.0 whose release source
    offers 0.2.0 through the installer (no bundle fingerprint on either
    side, so the installer path — the one the field case took)."""
    app = tmp_path / 'app'
    (app / '.venv').mkdir(parents=True)
    (app / 'unins000.exe').write_bytes(b'')
    monkeypatch.setattr(au.sys, 'platform', 'win32')
    monkeypatch.setattr(au.sys, 'prefix', str(app / '.venv'))
    _running(monkeypatch, '0.1.0')
    monkeypatch.setattr(
        au.update_windows.bundle,
        'bundle_file',
        lambda root=None: app / 'bundle.json',
    )
    return app


def _running(monkeypatch, version: str) -> None:
    monkeypatch.setattr(au, 'get_version', lambda: version)


def _source(app: Path, version='0.2.0', *, present=True) -> dict:
    """An installer offer whose bytes are there — or are not, which is
    the download failing."""
    src = app / f'setup-{version}.exe'
    if present:
        src.write_bytes(_BLOB)
    return {
        'version': version,
        'url': str(src),
        'size': len(_BLOB),
        'digest': None,
    }


def _arrives(offer: dict) -> None:
    Path(offer['url']).write_bytes(_BLOB)


def _offering(monkeypatch, offer) -> None:
    monkeypatch.setattr(wu, 'check_for_update', lambda: offer)


def _check_fails(monkeypatch) -> None:
    def boom():
        raise OSError('HTTP Error 504: Gateway Timeout')

    monkeypatch.setattr(wu, 'check_for_update', boom)


def _on_disk() -> dict | None:
    try:
        return json.loads(update_manual.path().read_text(encoding='utf-8'))
    except OSError:
        return None


class TestAFailedDownload:
    async def test_is_an_error_on_disk_in_the_log_and_due(
        self, windows, monkeypatch, caplog
    ):
        _offering(monkeypatch, _source(windows, present=False))
        with caplog.at_level(logging.WARNING, logger='app.update_manual'):
            await au.run_cycle()
        snap = au.snapshot()
        assert snap['state'] == au.ERROR
        assert snap['manual_due'] is True
        failure = snap['download_failure']
        assert failure['version'] == '0.2.0'
        assert failure['attempts'] == 1
        assert failure['path'] == pip_update.PATH_INSTALLER
        assert failure['stage'] in ('download', 'verify')
        assert failure['error']
        assert _on_disk()['version'] == '0.2.0'
        # The old behaviour said nothing at all; the log must now say it.
        assert 'could not be downloaded' in caplog.text

    async def test_failing_again_counts_another_attempt(
        self, windows, monkeypatch
    ):
        _offering(monkeypatch, _source(windows, present=False))
        await au.run_cycle()
        await au.run_cycle()
        assert au.snapshot()['download_failure']['attempts'] == 2
        assert _on_disk()['attempts'] == 2

    async def test_a_newer_release_failing_starts_counting_again(
        self, windows, monkeypatch
    ):
        _offering(monkeypatch, _source(windows, '0.2.0', present=False))
        await au.run_cycle()
        await au.run_cycle()
        _offering(monkeypatch, _source(windows, '0.3.0', present=False))
        await au.run_cycle()
        failure = au.snapshot()['download_failure']
        assert (failure['version'], failure['attempts']) == ('0.3.0', 1)

    async def test_a_failed_check_afterwards_keeps_the_guide(
        self, windows, monkeypatch
    ):
        """The field case: the download fails, and the next check cannot
        reach the release source either. Idle is right — nothing is in
        flight — but the guide must not be forgotten with the error."""
        _offering(monkeypatch, _source(windows, present=False))
        await au.run_cycle()
        _check_fails(monkeypatch)
        await au.run_cycle()
        snap = au.snapshot()
        assert snap['state'] == au.IDLE
        assert snap['error'] is None
        assert snap['manual_due'] is True
        assert snap['download_failure']['version'] == '0.2.0'

    async def test_it_outlives_a_restart(self, windows, monkeypatch):
        _offering(monkeypatch, _source(windows, present=False))
        await au.run_cycle()
        au.reset_for_tests()  # the server process is gone
        assert au.snapshot()['manual_due'] is False
        au.load_download_failure()  # what watch() does at boot
        snap = au.snapshot()
        assert snap['state'] == au.IDLE
        assert snap['manual_due'] is True
        assert snap['download_failure']['version'] == '0.2.0'


class TestWhenTheGuideStops:
    async def test_a_retry_that_downloads_shows_progress_not_the_guide(
        self, windows, monkeypatch
    ):
        _offering(monkeypatch, _source(windows, present=False))
        await au.run_cycle()
        au._update(state=au.DOWNLOADING, version='0.2.0')
        snap = au.snapshot()
        assert snap['manual_due'] is False
        # Still reported: the dialog may say a previous attempt failed.
        assert snap['download_failure']['version'] == '0.2.0'

    async def test_a_retry_that_succeeds_clears_it(self, windows, monkeypatch):
        offer = _source(windows, present=False)
        _offering(monkeypatch, offer)
        await au.run_cycle()
        await au.run_cycle()
        _arrives(offer)  # the network came back
        await au.run_cycle()
        snap = au.snapshot()
        assert snap['state'] == au.READY
        assert snap['manual_due'] is False
        assert snap['download_failure'] is None
        assert _on_disk() is None
        # And the in-app flow is what asks now: the installer pops.
        assert snap['popup_due'] is True

    async def test_updating_by_hand_ends_it(self, windows, monkeypatch):
        _offering(monkeypatch, _source(windows, present=False))
        await au.run_cycle()
        # The admin ran the installer; the server restarted on 0.2.0.
        au.reset_for_tests()
        _running(monkeypatch, '0.2.0')
        au.load_download_failure()
        snap = au.snapshot()
        assert snap['manual_due'] is False
        assert snap['download_failure'] is None
        assert _on_disk() is None  # a stale record is removed, not kept

    async def test_already_on_the_failed_release_says_nothing_even_unloaded(
        self, windows, monkeypatch
    ):
        """No restart in between: the in-memory entry itself goes quiet
        once the running version is no longer older."""
        _offering(monkeypatch, _source(windows, present=False))
        await au.run_cycle()
        _running(monkeypatch, '0.2.0')
        snap = au.snapshot()
        assert snap['manual_due'] is False
        assert snap['download_failure'] is None

    async def test_a_newer_running_version_than_the_failure_is_quiet(
        self, windows, monkeypatch
    ):
        _offering(monkeypatch, _source(windows, present=False))
        await au.run_cycle()
        au.reset_for_tests()
        _running(monkeypatch, '0.5.0')
        au.load_download_failure()
        assert au.snapshot()['download_failure'] is None


class TestThePipPaths:
    async def test_windows_pip_prefetch_failure_is_recorded(
        self, windows, monkeypatch
    ):
        offer = _source(windows)
        offer['bundle'] = {'url': 'mock://bundle', 'size': None}
        (windows / 'bundle.json').write_text(
            json.dumps({'fingerprint': 'f' * 64}), encoding='utf-8'
        )
        _offering(monkeypatch, offer)
        monkeypatch.setattr(
            wu, 'fetch_bundle', lambda o: {'fingerprint': 'f' * 64}
        )

        def warm(version):
            raise RuntimeError('uv could not reach the index')

        monkeypatch.setattr(au.pip_update, 'warm', warm)
        await au.run_cycle()
        snap = au.snapshot()
        assert snap['manual_due'] is True
        assert snap['download_failure']['path'] == pip_update.PATH_PIP
        assert snap['download_failure']['stage'] == 'prefetch'

    async def test_unix_prefetch_failure_is_recorded(
        self, tmp_path, monkeypatch
    ):
        src = tmp_path / 'pypi.json'
        src.write_text(
            json.dumps({'info': {'version': '0.9.0'}, 'releases': {}}),
            encoding='utf-8',
        )
        monkeypatch.setenv('VIBE_SELLER_PYPI_JSON_URL', str(src))
        _running(monkeypatch, '0.8.0')

        def warm(version):
            raise RuntimeError('uv could not reach the index')

        monkeypatch.setattr(au.pip_update, 'warm', warm)
        await au._cycle_unix()
        snap = au.snapshot()
        assert snap['state'] == au.ERROR
        assert snap['manual_due'] is True
        assert snap['download_failure']['version'] == '0.9.0'
        assert snap['download_failure']['path'] == pip_update.PATH_PIP


class TestTheRecord:
    def test_a_long_error_is_cut_to_a_line(self):
        entry = update_manual.note(
            '0.2.0', path_kind='installer', stage='download', error='x' * 5000
        )
        assert len(entry['error']) == update_manual.MAX_ERROR_CHARS

    def test_an_unreadable_file_is_no_record(self):
        update_manual.path().parent.mkdir(parents=True, exist_ok=True)
        update_manual.path().write_text('{not json', encoding='utf-8')
        assert update_manual.read() is None
        assert update_manual.load('0.1.0') is None

    def test_due_only_between_attempts(self):
        entry = {'version': '0.2.0'}
        assert update_manual.manual_due('idle', entry) is True
        assert update_manual.manual_due('error', entry) is True
        for state in ('downloading', 'ready', 'restarting'):
            assert update_manual.manual_due(state, entry) is False
        assert update_manual.manual_due('idle', None) is False


class TestTheRoute:
    def test_windows_gets_the_release_page(self):
        assert update_manual.route('windows', '0.3.6') == {
            'upgrade_command': None,
            'download_url': update_manual.RELEASES_PAGE_URL,
        }

    def test_a_nightly_lives_on_the_rolling_prerelease(self):
        assert (
            update_manual.route('windows', '0.3.7.dev202611260000')[
                'download_url'
            ]
            == update_manual.NIGHTLY_PAGE_URL
        )

    @pytest.mark.parametrize('platform', ['mac', 'linux', 'wsl'])
    def test_unix_gets_the_upgrade_command(self, platform):
        assert update_manual.route(platform, '0.3.6') == {
            'upgrade_command': 'vibe-seller upgrade',
            'download_url': None,
        }


def test_snapshot_carries_the_fields_the_ui_reads():
    snap = au.snapshot()
    assert snap['download_failure'] is None
    assert snap['manual_due'] is False
