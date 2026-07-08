"""Tests for `vibe-seller upgrade` — the in-place CLI updater.

Covers the default (PyPI) path and the `--test-pypi --version` release-
candidate path. Network + subprocess are mocked, so these run offline.
"""

import io
import json
from unittest.mock import MagicMock, patch

import pytest

import app.cli as cli

pytestmark = pytest.mark.unit


def _args(argv):
    return cli._build_parser().parse_args(argv)


class TestUpgradeArgParsing:
    def test_plain_upgrade_has_no_test_pypi(self):
        args = _args(['upgrade'])
        assert args.command == 'upgrade'
        assert args.test_pypi is False
        assert args.target_version is None

    def test_test_pypi_with_version(self):
        args = _args(['upgrade', '--test-pypi', '--version', '0.0.1.dev1'])
        assert args.test_pypi is True
        assert args.target_version == '0.0.1.dev1'


class TestCmdUpgradeDefault:
    def test_uses_uv_tool_upgrade_when_uv_present(self):
        with (
            patch.object(cli.shutil, 'which', return_value='/usr/bin/uv'),
            patch.object(cli.subprocess, 'run') as run,
        ):
            run.return_value = MagicMock(returncode=0)
            rc = cli._cmd_upgrade(_args(['upgrade']))
        assert rc == 0
        assert run.call_args[0][0] == ['uv', 'tool', 'upgrade', 'vibe-seller']

    def test_falls_back_to_pip_without_uv(self):
        with (
            patch.object(cli.shutil, 'which', return_value=None),
            patch.object(cli.subprocess, 'run') as run,
        ):
            run.return_value = MagicMock(returncode=0)
            cli._cmd_upgrade(_args(['upgrade']))
        cmd = run.call_args[0][0]
        assert cmd[1:] == ['-m', 'pip', 'install', '--upgrade', 'vibe-seller']


class TestCmdUpgradeTestPypi:
    def test_requires_version(self):
        with patch.object(cli.subprocess, 'run') as run:
            rc = cli._cmd_upgrade(_args(['upgrade', '--test-pypi']))
        assert rc == 2
        run.assert_not_called()

    def test_requires_uv(self):
        args = _args(['upgrade', '--test-pypi', '--version', '0.0.1.dev1'])
        with patch.object(cli.shutil, 'which', return_value=None):
            rc = cli._cmd_upgrade(args)
        assert rc == 2

    def test_downloads_wheel_and_force_installs(self):
        wheel = 'vibe_seller-0.0.1.dev2-py3-none-any.whl'
        meta = {
            'urls': [
                {'packagetype': 'sdist', 'url': 'https://x/pkg.tar.gz'},
                {'packagetype': 'bdist_wheel', 'url': f'https://x/{wheel}'},
            ]
        }
        responses = [
            io.BytesIO(json.dumps(meta).encode()),  # metadata fetch
            io.BytesIO(b'PK-fake-wheel-bytes'),  # wheel download
        ]
        with (
            patch.object(cli.shutil, 'which', return_value='/usr/bin/uv'),
            patch.object(cli.urllib.request, 'urlopen', side_effect=responses),
            patch.object(cli.subprocess, 'run') as run,
        ):
            run.return_value = MagicMock(returncode=0)
            args = _args(['upgrade', '--test-pypi', '--version', '0.0.1.dev2'])
            rc = cli._cmd_upgrade(args)
        assert rc == 0
        cmd = run.call_args[0][0]
        assert cmd[:4] == ['uv', 'tool', 'install', '--force']
        assert cmd[4].endswith(wheel)  # real wheel filename preserved

    def test_missing_wheel_returns_error(self):
        meta = {'urls': [{'packagetype': 'sdist', 'url': 'https://x/s.tgz'}]}
        with (
            patch.object(cli.shutil, 'which', return_value='/usr/bin/uv'),
            patch.object(
                cli.urllib.request,
                'urlopen',
                return_value=io.BytesIO(json.dumps(meta).encode()),
            ),
            patch.object(cli.subprocess, 'run') as run,
        ):
            args = _args(['upgrade', '--test-pypi', '--version', '0.0.1.dev9'])
            rc = cli._cmd_upgrade(args)
        assert rc == 1  # no bdist_wheel in urls
        run.assert_not_called()
