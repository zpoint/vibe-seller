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
        assert run.call_args_list[0][0][0] == [
            'uv',
            'tool',
            'upgrade',
            'vibe-seller',
        ]

    def test_falls_back_to_pip_without_uv(self):
        with (
            patch.object(cli.shutil, 'which', return_value=None),
            patch.object(cli.subprocess, 'run') as run,
        ):
            run.return_value = MagicMock(returncode=0)
            cli._cmd_upgrade(_args(['upgrade']))
        cmd = run.call_args_list[0][0][0]
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
        cmd = run.call_args_list[0][0][0]
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


class TestUpgradeClaudeCli:
    """An upgrade moves the project-local Claude Code CLI too — the copy
    every agent task spawns, which a global upgrade never reaches."""

    def _run(self, tmp_path, monkeypatch, *, which, rcs, argv=('upgrade',)):
        monkeypatch.setenv('VIBE_HOME', str(tmp_path))
        with (
            patch.object(cli.shutil, 'which', side_effect=which),
            patch.object(cli.subprocess, 'run') as run,
        ):
            run.side_effect = [MagicMock(returncode=rc) for rc in rcs]
            rc = cli._cmd_upgrade(_args(list(argv)))
        return rc, [c[0][0] for c in run.call_args_list]

    def test_upgrades_the_project_local_cli_after_the_package(
        self, tmp_path, monkeypatch
    ):
        rc, cmds = self._run(
            tmp_path,
            monkeypatch,
            which=lambda n: f'/usr/bin/{n}',
            rcs=[0, 0],
        )
        assert rc == 0
        assert cmds[0] == ['uv', 'tool', 'upgrade', 'vibe-seller']
        assert cmds[1] == [
            '/usr/bin/npm',
            'install',
            '--prefix',
            str(tmp_path),
            '--no-save',
            '@anthropic-ai/claude-code@latest',
        ]

    def test_cli_is_upgraded_even_when_the_package_is_current(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(cli, '_upgrade_cmd', lambda **_: None)
        rc, cmds = self._run(
            tmp_path, monkeypatch, which=lambda n: f'/usr/bin/{n}', rcs=[0]
        )
        assert rc == 0
        assert len(cmds) == 1
        assert cmds[0][-1] == '@anthropic-ai/claude-code@latest'

    def test_a_failed_package_upgrade_leaves_the_cli_alone(
        self, tmp_path, monkeypatch
    ):
        rc, cmds = self._run(
            tmp_path, monkeypatch, which=lambda n: f'/usr/bin/{n}', rcs=[1]
        )
        assert rc == 1
        assert len(cmds) == 1

    def test_a_failed_cli_upgrade_fails_the_command(
        self, tmp_path, monkeypatch
    ):
        rc, _ = self._run(
            tmp_path, monkeypatch, which=lambda n: f'/usr/bin/{n}', rcs=[0, 1]
        )
        assert rc == 1

    def test_without_npm_the_cli_step_is_skipped(self, tmp_path, monkeypatch):
        rc, cmds = self._run(
            tmp_path,
            monkeypatch,
            which=lambda n: '/usr/bin/uv' if n == 'uv' else None,
            rcs=[0],
        )
        assert rc == 0
        assert cmds == [['uv', 'tool', 'upgrade', 'vibe-seller']]
