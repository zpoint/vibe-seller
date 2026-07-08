"""Tests for app.windows_update — the in-place Windows updater.

The fetch/download/run are mocked, so these run on all platforms and
prove the version comparison + flow without touching the network.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import app.windows_update as wu

pytestmark = pytest.mark.unit


class TestIsNewer:
    def test_release_beats_its_dev_build(self):
        assert wu.is_newer('0.1.0', '0.1.0.dev3+gabc123')

    def test_higher_beats_lower(self):
        assert wu.is_newer('0.0.2', '0.0.1')

    def test_lower_is_not_newer(self):
        assert not wu.is_newer('0.0.1', '0.0.2')

    def test_equal_is_not_newer(self):
        assert not wu.is_newer('0.0.1', '0.0.1')

    def test_unparseable_is_not_newer(self):
        assert not wu.is_newer('not-a-version', '0.0.1')


_ASSET = {
    'name': 'VibeSeller-Setup.exe',
    'browser_download_url': 'http://x/VibeSeller-Setup.exe',
}


class TestCheckForUpdate:
    def _check(self, payload, current='0.1.0'):
        with (
            patch.object(wu, '_fetch_release', return_value=payload),
            patch.object(wu, 'get_version', return_value=current),
        ):
            return wu.check_for_update()

    def test_newer_release_found(self):
        payload = {'tag_name': 'v0.2.0', 'assets': [_ASSET]}
        assert self._check(payload) == {
            'version': '0.2.0',
            'url': _ASSET['browser_download_url'],
        }

    def test_same_version_returns_none(self):
        assert self._check({'tag_name': 'v0.1.0', 'assets': [_ASSET]}) is None

    def test_missing_asset_returns_none(self):
        assert self._check({'tag_name': 'v0.2.0', 'assets': []}) is None

    def test_assets_unwrapped_to_object_still_works(self):
        # PowerShell ConvertTo-Json may emit a 1-item array as an object.
        assert self._check({'tag_name': 'v0.2.0', 'assets': _ASSET}) == {
            'version': '0.2.0',
            'url': _ASSET['browser_download_url'],
        }


class TestFetchReleaseLocalPath:
    def test_reads_plain_local_path(self, tmp_path, monkeypatch):
        rel = {'tag_name': 'v0.2.0', 'assets': [_ASSET]}
        p = tmp_path / 'release.json'
        p.write_text(json.dumps(rel), encoding='utf-8')
        monkeypatch.setenv('VIBE_SELLER_RELEASES_URL', str(p))
        assert wu._fetch_release() == rel


class TestUpgrade:
    def test_up_to_date(self):
        with (
            patch.object(wu, 'check_for_update', return_value=None),
            patch.object(wu, 'get_version', return_value='0.1.0'),
        ):
            assert wu.upgrade()['status'] == 'up-to-date'

    def test_updating_downloads_and_runs(self, tmp_path):
        upd = {'version': '0.2.0', 'url': _ASSET['browser_download_url']}
        with (
            patch.object(wu, 'check_for_update', return_value=upd),
            patch.object(
                wu, 'download_installer', return_value=tmp_path / 's.exe'
            ),
            patch.object(wu, 'run_installer') as run,
        ):
            res = wu.upgrade()
        assert res == {
            'status': 'updating',
            'version': '0.2.0',
            'notes_url': wu.RELEASES_PAGE,
        }
        run.assert_called_once()

    def test_error_returns_manual_url(self):
        with patch.object(
            wu, 'check_for_update', side_effect=RuntimeError('boom')
        ):
            res = wu.upgrade()
        assert res['status'] == 'error'
        assert res['manual_url'] == wu.MANUAL_URL
        assert 'boom' in res['error']

    def test_downloads_to_a_unique_dir_not_fixed_temp(self, tmp_path):
        # A fixed %TEMP%\VibeSeller-Setup.exe collides when two upgrades
        # race → WinError 32 on launch (the field bug). Each run must get
        # its own dir.
        upd = {'version': '0.2.0', 'url': _ASSET['browser_download_url']}
        seen: list[Path] = []
        with (
            patch.object(wu, 'check_for_update', return_value=upd),
            patch.object(wu, 'run_installer'),
            patch.object(
                wu,
                'download_installer',
                side_effect=lambda _url, dest: seen.append(dest) or dest,
            ),
        ):
            wu.upgrade()
            wu.upgrade()
        assert len(seen) == 2
        assert seen[0].name == wu.ASSET_NAME
        assert seen[0].parent != seen[1].parent  # unique dir per run

    def test_single_flight_reports_in_progress(self):
        # A second concurrent click must not launch a rival installer.
        assert wu._upgrade_lock.acquire(blocking=False)
        try:
            res = wu.upgrade()
        finally:
            wu._upgrade_lock.release()
        assert res['status'] == 'in-progress'


class TestFetchReleaseNotes:
    def _patch_releases(self, payload):
        # fetch_release_notes reads _open(...).read() — mock that.
        class _Resp:
            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                return False

            def read(self_):
                return json.dumps(payload).encode()

        return patch.object(wu, '_open', return_value=_Resp())

    _RELS = [
        {'tag_name': 'v0.0.10', 'html_url': 'https://x/0.0.10'},
        {'tag_name': 'v0.0.9', 'html_url': 'https://x/0.0.9'},
        {'tag_name': 'v0.0.8', 'html_url': 'https://x/0.0.8'},
    ]

    def test_lists_every_release_newer_than_current(self):
        # 0.0.8 -> latest must surface BOTH skipped releases, newest first.
        with self._patch_releases(self._RELS):
            notes = wu.fetch_release_notes('0.0.8')
        assert [n['version'] for n in notes] == ['0.0.10', '0.0.9']

    def test_skips_drafts_and_prereleases(self):
        # GitHub returns newest-first; the function preserves that order.
        rels = [
            {'tag_name': 'v0.0.11', 'html_url': 'https://x/0.0.11'},
            {**self._RELS[0], 'prerelease': True},  # 0.0.10 prerelease
            {**self._RELS[1], 'draft': True},  # 0.0.9 draft
            self._RELS[2],  # 0.0.8
        ]
        with self._patch_releases(rels):
            notes = wu.fetch_release_notes('0.0.7')
        assert [n['version'] for n in notes] == ['0.0.11', '0.0.8']

    def test_caps_at_limit(self):
        with self._patch_releases(self._RELS):
            notes = wu.fetch_release_notes('0.0.0', limit=1)
        assert [n['version'] for n in notes] == ['0.0.10']

    def test_fetch_failure_returns_empty(self):
        with patch.object(wu, '_open', side_effect=OSError('boom')):
            assert wu.fetch_release_notes('0.0.8') == []


class TestRunInstaller:
    def test_retries_on_winerror32_then_succeeds(self, tmp_path):
        # A freshly-written exe can be briefly locked (AV scan / racing
        # download); retry rather than fail the whole upgrade.
        calls = {'n': 0}

        def flaky(*_a, **_k):
            calls['n'] += 1
            if calls['n'] < 3:
                raise PermissionError(32, 'in use')

        with (
            patch.object(wu.subprocess, 'Popen', side_effect=flaky) as popen,
            patch.object(wu.time, 'sleep'),
        ):
            wu.run_installer(tmp_path / 's.exe')
        assert popen.call_count == 3

    def test_raises_after_exhausting_retries(self, tmp_path):
        with (
            patch.object(
                wu.subprocess,
                'Popen',
                side_effect=PermissionError(32, 'in use'),
            ),
            patch.object(wu.time, 'sleep'),
            pytest.raises(PermissionError),
        ):
            wu.run_installer(tmp_path / 's.exe')
