"""Tests for app.windows_update — the in-place Windows updater.

The fetch/download/run are mocked, so these run on all platforms and
prove the version comparison + flow without touching the network.
"""

import json
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
        assert res == {'status': 'updating', 'version': '0.2.0'}
        run.assert_called_once()

    def test_error_returns_manual_url(self):
        with patch.object(
            wu, 'check_for_update', side_effect=RuntimeError('boom')
        ):
            res = wu.upgrade()
        assert res['status'] == 'error'
        assert res['manual_url'] == wu.MANUAL_URL
        assert 'boom' in res['error']
