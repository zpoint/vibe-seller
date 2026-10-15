"""Unit tests for ``app.release_channel`` and every path that consumes it.

The contract: a stable install is never offered a nightly — not by the
web UI's update check, the Windows tray, or `vibe-seller upgrade` — while
a nightly install is offered the highest newer nightly or stable build.
Dev/source checkouts are never checked by the web UI.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import app.cli as cli
from app.release_channel import best_upgrade, channel_of
import app.routers.system as system_router
import app.update_check as uc
import app.windows_update as wu

pytestmark = pytest.mark.unit

NIGHTLY = '0.2.1.dev202610161800'
OLDER_NIGHTLY = '0.2.1.dev202610151800'
PYPI_VERSIONS = ['0.1.0', '0.2.0', OLDER_NIGHTLY, NIGHTLY]


class TestChannelOf:
    @pytest.mark.parametrize(
        ('version', 'channel'),
        [
            ('0.2.0', 'stable'),
            ('0.3.0rc1', 'stable'),  # unchanged: rc was always checked
            (NIGHTLY, 'nightly'),
            ('0.2.1.dev20261016', 'nightly'),  # date-only stamp
            ('0.2.1.dev3+gabc1234', 'dev'),  # setuptools-scm checkout
            ('0.2.1.dev3', 'dev'),  # commit count, not a timestamp
            ('0.0.1.dev42+gabc1234', 'dev'),  # PR installer build
            ('0.0.0+dev', 'dev'),
            ('not-a-version', 'dev'),
        ],
    )
    def test_classification(self, version, channel):
        assert channel_of(version) == channel

    def test_nightly_sorts_between_stables(self):
        assert best_upgrade([NIGHTLY], '0.2.0', channel='nightly') == NIGHTLY
        assert best_upgrade(['0.2.1'], NIGHTLY) == '0.2.1'


class TestBestUpgrade:
    def test_stable_is_never_offered_a_nightly(self):
        assert best_upgrade(PYPI_VERSIONS, '0.2.0') is None

    def test_stable_still_gets_newer_stable(self):
        assert best_upgrade([*PYPI_VERSIONS, '0.3.0'], '0.2.0') == '0.3.0'

    def test_nightly_gets_newer_nightly(self):
        assert best_upgrade(PYPI_VERSIONS, OLDER_NIGHTLY) == NIGHTLY

    def test_nightly_gets_stable_when_it_is_higher(self):
        assert best_upgrade([*PYPI_VERSIONS, '0.2.1'], NIGHTLY) == '0.2.1'

    def test_nightly_up_to_date(self):
        assert best_upgrade(PYPI_VERSIONS, NIGHTLY) is None

    def test_dev_is_offered_nothing(self):
        assert best_upgrade(PYPI_VERSIONS, '0.2.1.dev3+gabc') is None


def _pypi_client(versions, *, yanked=()):
    payload = {
        'info': {'version': '0.2.0'},
        'releases': {
            v: [{'yanked': v in yanked, 'packagetype': 'bdist_wheel'}]
            for v in versions
        },
    }
    return httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json=payload)
        )
    )


class TestWebUpdateCheck:
    async def test_fetch_nightly_upgrade_skips_yanked(self):
        async with _pypi_client(PYPI_VERSIONS, yanked={NIGHTLY}) as client:
            got = await uc.fetch_nightly_upgrade(
                client, '0.2.1.dev202610141800'
            )
        assert got == OLDER_NIGHTLY

    @pytest.fixture(autouse=True)
    def _cold_cache(self):
        system_router._update_check_cache = None
        yield
        system_router._update_check_cache = None

    async def test_stable_install_never_reads_the_nightly_list(
        self, monkeypatch
    ):
        nightly_lookup = AsyncMock(return_value=NIGHTLY)
        monkeypatch.setattr(system_router, 'APP_VERSION', '0.2.0')
        monkeypatch.setattr(
            system_router,
            'fetch_latest_pypi_version',
            AsyncMock(return_value='0.2.0'),
        )
        monkeypatch.setattr(
            system_router, 'fetch_nightly_upgrade', nightly_lookup
        )

        result = await system_router.update_check()

        assert result['update_available'] is False
        assert result['channel'] == 'stable'
        nightly_lookup.assert_not_awaited()

    async def test_nightly_install_offered_newer_nightly_on_windows(
        self, monkeypatch
    ):
        monkeypatch.setattr(system_router, 'APP_VERSION', OLDER_NIGHTLY)
        monkeypatch.setattr(
            system_router,
            'fetch_nightly_upgrade',
            AsyncMock(return_value=NIGHTLY),
        )
        monkeypatch.setattr(
            system_router, 'fetch_release_notes', AsyncMock(return_value=[])
        )
        monkeypatch.setattr(system_router, 'get_platform', lambda: 'windows')

        result = await system_router.update_check()

        assert result['update_available'] is True
        assert result['channel'] == 'nightly'
        assert result['latest_version'] == NIGHTLY
        assert result['download_url'] == system_router.NIGHTLY_PAGE_URL

    async def test_nightly_install_offered_stable_links_stable_page(
        self, monkeypatch
    ):
        monkeypatch.setattr(system_router, 'APP_VERSION', NIGHTLY)
        monkeypatch.setattr(
            system_router,
            'fetch_nightly_upgrade',
            AsyncMock(return_value='0.2.1'),
        )
        monkeypatch.setattr(
            system_router, 'fetch_release_notes', AsyncMock(return_value=[])
        )
        monkeypatch.setattr(system_router, 'get_platform', lambda: 'windows')

        result = await system_router.update_check()

        assert result['download_url'] == system_router.RELEASES_PAGE_URL


_EXE = 'VibeSeller-Setup.exe'


def _release(tmp_path, name, payload):
    path = tmp_path / f'{name}.json'
    path.write_text(json.dumps(payload))
    return str(path)


class TestWindowsTray:
    @pytest.fixture
    def releases(self, tmp_path, monkeypatch):
        stable = {
            'tag_name': 'v0.2.0',
            'assets': [{'name': _EXE, 'browser_download_url': 'http://s/exe'}],
        }
        nightly = {
            'tag_name': 'nightly',
            'name': NIGHTLY,
            'prerelease': True,
            'assets': [{'name': _EXE, 'browser_download_url': 'http://n/exe'}],
        }
        monkeypatch.setenv(
            'VIBE_SELLER_RELEASES_URL', _release(tmp_path, 'stable', stable)
        )
        monkeypatch.setenv(
            'VIBE_SELLER_NIGHTLY_RELEASES_URL',
            _release(tmp_path, 'nightly', nightly),
        )

    def _check(self, current):
        with patch.object(wu, 'get_version', return_value=current):
            return wu.check_for_update()

    def test_stable_install_ignores_the_nightly(self, releases):
        assert self._check('0.2.0') is None

    def test_nightly_install_gets_the_newer_nightly(self, releases):
        assert self._check(OLDER_NIGHTLY) == {
            'version': NIGHTLY,
            'url': 'http://n/exe',
        }

    def test_pr_build_keeps_the_old_rule(self, releases):
        # windows-upgrade.yml upgrades a PR build to releases/latest.
        assert self._check('0.0.1.dev42+gabc1234') == {
            'version': '0.2.0',
            'url': 'http://s/exe',
        }


class TestCliUpgrade:
    def _run(self, argv, current, versions=PYPI_VERSIONS):
        with (
            patch.object(cli, '_resolve_version', return_value=current),
            patch.object(cli, '_pypi_versions', return_value=versions),
            patch.object(cli.shutil, 'which', return_value='/usr/bin/uv'),
            patch.object(cli.subprocess, 'run') as run,
        ):
            run.return_value = MagicMock(returncode=0)
            rc = cli._cmd_upgrade(cli._build_parser().parse_args(argv))
        return rc, run.call_args[0][0] if run.call_args else None

    def test_stable_upgrade_is_unchanged(self):
        assert self._run(['upgrade'], '0.2.0') == (
            0,
            ['uv', 'tool', 'upgrade', 'vibe-seller'],
        )

    def test_nightly_pins_the_exact_version_not_prerelease_allow(self):
        # --prerelease allow would also admit alpha dependencies.
        rc, cmd = self._run(['upgrade'], OLDER_NIGHTLY)
        assert cmd == [
            'uv',
            'tool',
            'install',
            '--force',
            f'vibe-seller=={NIGHTLY}',
        ]
        assert '--prerelease' not in cmd

    def test_stable_opts_into_nightly(self):
        _, cmd = self._run(['upgrade', '--nightly'], '0.2.0')
        assert cmd[-1] == f'vibe-seller=={NIGHTLY}'

    def test_back_to_stable_reinstalls_unpinned(self):
        _, cmd = self._run(['upgrade', '--stable'], NIGHTLY)
        assert cmd == ['uv', 'tool', 'install', '--force', 'vibe-seller']

    def test_nightly_up_to_date_runs_nothing(self):
        assert self._run(['upgrade'], NIGHTLY) == (0, None)
