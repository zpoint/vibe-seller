"""Tests for `vibe-seller upgrade` — the in-place CLI updater.

Covers the default (PyPI) path and the `--test-pypi --version` release-
candidate path. Network + subprocess are mocked, so these run offline.
"""

import io
import json
from unittest.mock import MagicMock, patch

import pytest

from app import bundle
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
        # The browser-engine step between the two has its own tests
        # (test_chromium_engine.py); keep these about the CLI sequence.
        monkeypatch.setattr(cli, '_install_browser_after_upgrade', lambda: None)
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
        # The version the bundle manifest pins, not npm's latest: the
        # Windows bundle installs the same number, so every platform
        # runs the CLI the release was tested with.
        assert cmds[1] == [
            '/usr/bin/npm',
            'install',
            '--prefix',
            str(tmp_path),
            '--no-save',
            f'@anthropic-ai/claude-code@{bundle.pinned_claude_version()}',
        ]

    def test_the_cli_version_follows_the_manifest(self, tmp_path, monkeypatch):
        """The pin has ONE source. A bump of app/bundle_pins.json must
        move `vibe-seller upgrade` with it — if this test needed an edit
        somewhere else too, that would be a second source."""
        pins = tmp_path / 'repo'
        (pins / 'app').mkdir(parents=True)
        (pins / 'app' / 'bundle_pins.json').write_text(
            json.dumps({
                'format': 1,
                'claude_code': {'version': '9.9.9'},
                'playwright': {'version': '1.65.1'},
            }),
            encoding='utf-8',
        )
        monkeypatch.setattr(bundle, 'repo_root', lambda: pins)
        monkeypatch.setattr(cli, '_upgrade_cmd', lambda **_: None)
        rc, cmds = self._run(
            tmp_path, monkeypatch, which=lambda n: f'/usr/bin/{n}', rcs=[0]
        )
        assert rc == 0
        assert cmds[0][-1] == '@anthropic-ai/claude-code@9.9.9'

    def test_cli_is_upgraded_even_when_the_package_is_current(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(cli, '_upgrade_cmd', lambda **_: None)
        rc, cmds = self._run(
            tmp_path, monkeypatch, which=lambda n: f'/usr/bin/{n}', rcs=[0]
        )
        assert rc == 0
        assert len(cmds) == 1
        assert cmds[0][-1] == (
            f'@anthropic-ai/claude-code@{bundle.pinned_claude_version()}'
        )

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


class TestUpgradeWaitsForTheServer:
    """`vibe-seller upgrade` does not finish while the old code serves:
    it waits for each server it started to come back on the new version
    (the server restarts itself — app/upgrade_restart.py)."""

    PORT = 7899

    @pytest.fixture(autouse=True)
    def _home(self, tmp_path, monkeypatch):
        monkeypatch.setenv('VIBE_HOME', str(tmp_path))
        (tmp_path / 'pids').mkdir()
        (tmp_path / 'pids' / f'backend_{self.PORT}.pid').write_text('4242')
        monkeypatch.setattr(cli, '_pid_alive', lambda p: p == 4242)
        monkeypatch.setattr(cli, '_new_cli_version', lambda: '2.0.0')
        monkeypatch.setattr(cli.time, 'sleep', lambda _s: None)
        # A fake clock: each probe advances it, so the waits are bounded.
        clock = iter(range(0, 100_000, 1))
        monkeypatch.setattr(cli.time, 'monotonic', lambda: next(clock))

    def _serve(self, monkeypatch, *bodies):
        """Probe answers in order; the last one repeats."""
        seq = list(bodies)
        monkeypatch.setattr(
            cli,
            '_probe_version',
            lambda _port: seq.pop(0) if len(seq) > 1 else seq[0],
        )

    def test_returns_once_the_server_runs_the_new_version(
        self, monkeypatch, capsys
    ):
        self._serve(
            monkeypatch,
            {'version': '1.0.0', 'pending_version': None},
            {'version': '1.0.0', 'pending_version': '2.0.0'},
            None,  # mid-restart: nothing answering
            {'version': '2.0.0', 'pending_version': None},
        )
        assert cli._await_servers_upgraded('1.0.0') == 0
        assert 'now running 2.0.0' in capsys.readouterr().out

    def test_a_server_that_never_picks_it_up_fails_the_upgrade(
        self, monkeypatch, capsys
    ):
        self._serve(monkeypatch, {'version': '1.0.0', 'pending_version': None})
        assert cli._await_servers_upgraded('1.0.0') == 1
        assert f'vibe-seller stop --port {self.PORT}' in capsys.readouterr().err

    def test_draining_for_running_tasks_is_reported_not_failed(
        self, monkeypatch, capsys
    ):
        self._serve(
            monkeypatch, {'version': '1.0.0', 'pending_version': '2.0.0'}
        )
        assert cli._await_servers_upgraded('1.0.0') == 0
        assert 'restarts onto 2.0.0 by itself' in capsys.readouterr().out

    def test_a_server_too_old_to_restart_itself_gets_the_command(
        self, monkeypatch, capsys
    ):
        self._serve(monkeypatch, {'version': '1.0.0'})
        assert cli._await_servers_upgraded('1.0.0') == 0
        assert f'vibe-seller stop --port {self.PORT}' in capsys.readouterr().out

    def test_nothing_to_wait_for_when_the_version_did_not_change(
        self, monkeypatch
    ):
        monkeypatch.setattr(cli, '_new_cli_version', lambda: '1.0.0')
        self._serve(monkeypatch, {'version': '1.0.0', 'pending_version': None})
        assert cli._await_servers_upgraded('1.0.0') == 0

    def test_no_running_server_runs_nothing(self, tmp_path, monkeypatch):
        (tmp_path / 'pids' / f'backend_{self.PORT}.pid').unlink()
        with patch.object(cli.subprocess, 'run') as run:
            assert cli._await_servers_upgraded('1.0.0') == 0
        run.assert_not_called()


class TestUpgradeVersionFlag:
    """`--version X` means "install X", on the ordinary path too.

    The mechanics of an exact install — the pinned argv, the receipt
    repair that has to follow it — are `app/uv_tool.py`'s and are tested
    there. What belongs here is the routing, because a `--version` that
    is silently ignored is the bug: the command looks pinned, the apply
    installs the newest thing in the index, and the update record
    describes a version nobody installed.
    """

    def test_the_flag_selects_the_exact_install(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            cli.uv_tool, 'install_exactly', lambda v: seen.append(v) or 0
        )
        monkeypatch.setattr(cli, '_install_browser_after_upgrade', lambda: None)
        monkeypatch.setattr(cli, '_upgrade_claude_cli', lambda: 0)
        monkeypatch.setattr(cli, '_await_servers_upgraded', lambda *_: 0)
        assert cli._cmd_upgrade(_args(['upgrade', '--version', '99.0.2'])) == 0
        assert seen == ['99.0.2']

    def test_without_the_flag_the_plain_upgrade_is_untouched(self, monkeypatch):
        def explode(*_a, **_k):
            raise AssertionError('an exact install was run for a plain upgrade')

        monkeypatch.setattr(cli.uv_tool, 'install_exactly', explode)
        with (
            patch.object(cli.shutil, 'which', return_value='/usr/bin/uv'),
            patch.object(cli.subprocess, 'run') as run,
        ):
            run.return_value = MagicMock(returncode=0)
            monkeypatch.setattr(
                cli, '_install_browser_after_upgrade', lambda: None
            )
            monkeypatch.setattr(cli, '_upgrade_claude_cli', lambda: 0)
            monkeypatch.setattr(cli, '_await_servers_upgraded', lambda *_: 0)
            cli._cmd_upgrade(_args(['upgrade']))
        assert run.call_args_list[0][0][0] == [
            'uv',
            'tool',
            'upgrade',
            'vibe-seller',
        ]
