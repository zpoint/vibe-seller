"""Handing the machine to the detached apply helper.

The seam these tests sit either side of: ``app/pip_update.py`` decides
whether the pip path is safe and proves it offline; ``app/apply_launch.py``
writes the ONE document the helper will live by and starts it outside
the install being replaced. What is pinned here is that the document
carries the truth about this process — the command that was proven, the
port that is actually bound, who owns the server, the single-flight
lock — because the helper cannot ask anything once the server it came
from is stopped.
"""

import json
from pathlib import Path

import pytest

import app.apply_launch as al
import app.pip_update as pu

pytestmark = pytest.mark.unit


class TestApplyHandoff:
    @pytest.fixture(autouse=True)
    def _unlocked(self, monkeypatch):
        """These test the handoff, not the lock (which has its own)."""
        monkeypatch.setattr(pu, 'apply_in_progress', lambda: None)

    def test_the_helper_is_copied_out_of_the_package(
        self, bundle_home, tmp_path, monkeypatch
    ):
        """It must not run from the venv it is about to replace."""
        monkeypatch.setattr(
            pu, 'updates_dir', lambda: tmp_path / 'home' / 'updates'
        )
        monkeypatch.setattr(al, '_server_port', lambda: 7792)
        started = []
        monkeypatch.setattr(
            al, '_popen_detached', lambda argv, log: started.append(argv)
        )
        proc = al.apply('0.3.2', pu.TRIGGER_AUTO_IDLE, pu.PATH_PIP)
        del proc
        helper = tmp_path / 'home' / 'updates' / al.HELPER_MODULE
        assert helper.is_file()
        assert helper.read_text(encoding='utf-8') == Path(
            al.__file__
        ).with_name(al.HELPER_MODULE).read_text(encoding='utf-8')
        assert str(helper) in started[0]
        # Run by the base interpreter, never by the venv being replaced.
        assert started[0][0] == pu.prefetch_python()
        assert started[0][0] != pu.sys.executable

    def test_the_proven_command_is_the_one_handed_over(
        self, bundle_home, tmp_path, monkeypatch
    ):
        """The offline dry run is the promise; the helper must run that
        exact argv rather than rebuild one that could differ."""
        monkeypatch.setattr(pu, 'updates_dir', lambda: tmp_path / 'u')
        monkeypatch.setattr(al, '_server_port', lambda: 7792)
        monkeypatch.setattr(al, '_popen_detached', lambda argv, log: object())
        al.apply('0.3.2', pu.TRIGGER_AUTO_IDLE, pu.PATH_PIP)
        args = json.loads(
            (tmp_path / 'u' / al.HELPER_ARGS_NAME).read_text(encoding='utf-8')
        )
        assert args['install_command'] == pu.apply_command('0.3.2')
        assert args['from_version'] == pu.get_version()
        assert args['target_version'] == '0.3.2'
        assert args['platform'] == 'win32'
        assert args['path'] == pu.PATH_PIP

    def test_the_app_home_and_port_carry_through(
        self, bundle_home, tmp_path, monkeypatch
    ):
        """Start the tray with a different VIBE_HOME and the update
        "succeeds" on a machine that is not the one the user has."""
        from app.config import VIBE_SELLER_DIR  # noqa: PLC0415

        monkeypatch.setattr(pu, 'updates_dir', lambda: tmp_path / 'u')
        monkeypatch.setattr(al, '_server_port', lambda: 7792)
        monkeypatch.setattr(al, '_popen_detached', lambda argv, log: object())
        al.apply('0.3.2', pu.TRIGGER_MANUAL, pu.PATH_PIP)
        args = json.loads(
            (tmp_path / 'u' / al.HELPER_ARGS_NAME).read_text(encoding='utf-8')
        )
        assert args['env']['VIBE_HOME'] == str(VIBE_SELLER_DIR)
        assert args['env']['VIBE_SELLER_PORT'] == '7792'
        assert args['port'] == 7792
        assert args['trigger'] == pu.TRIGGER_MANUAL

    def test_posix_hands_over_the_real_upgrade_command(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(pu.sys, 'platform', 'linux')
        monkeypatch.setattr(pu, 'updates_dir', lambda: tmp_path / 'u')
        monkeypatch.setattr(al, '_server_port', lambda: 7792)
        monkeypatch.setattr(al, '_popen_detached', lambda argv, log: object())
        al.apply('0.9.0', pu.TRIGGER_AUTO_IDLE, pu.PATH_PIP)
        args = json.loads(
            (tmp_path / 'u' / al.HELPER_ARGS_NAME).read_text(encoding='utf-8')
        )
        # The REAL upgrade command (one source of truth for what an
        # upgrade does: package, Chromium, Claude Code CLI) — and the
        # version the update check approved is IN it. Without it,
        # `uv tool upgrade` answers a different question, "what is
        # newest in the sources right now", and can install a release
        # nothing offered: the Linux end-to-end measured exactly that
        # on 2026-11-13 (a newer, broken wheel in the same find-links
        # directory was installed, and `last.json` described the update
        # to 99.0.2 that never happened). See app/cli.py
        # `_upgrade_to_exact`.
        assert args['install_command'][-3:] == [
            'upgrade',
            '--version',
            '0.9.0',
        ]
        assert args['start_command'][-3:] == ['start', '--port', '7792']
        assert args['stop_command'][-3:] == ['stop', '--port', '7792']

    def test_the_unix_apply_names_the_version_it_approved(self, monkeypatch):
        """Windows passes the gated argv verbatim; the Unix path is
        allowed to be a COMMAND the CLI owns, but not an unpinned
        "give me whatever is newest"."""
        monkeypatch.setattr(pu.sys, 'platform', 'linux')
        argv = al.install_command('99.0.2', pu.PATH_PIP)
        assert argv[-2:] == ['--version', '99.0.2']
        assert 'upgrade' in argv


class TestSupervisorDetection:
    """A wrong answer here is a dark machine after a failed update, so
    the false case is the one that matters most."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        for name in ('INVOCATION_ID', 'NOTIFY_SOCKET', 'XPC_SERVICE_NAME'):
            monkeypatch.delenv(name, raising=False)

    def test_a_plain_shell_is_unsupervised(self):
        assert al.supervisor() is None

    def test_systemd_unit(self, monkeypatch):
        monkeypatch.setenv('INVOCATION_ID', 'a1b2c3')
        assert al.supervisor() == 'systemd'

    def test_systemd_notify_socket(self, monkeypatch):
        monkeypatch.setenv('NOTIFY_SOCKET', '/run/systemd/notify')
        assert al.supervisor() == 'systemd'

    def test_a_launchd_job_label_is_supervised(self, monkeypatch):
        # The label docs/macos-setup.md installs.
        monkeypatch.setenv('XPC_SERVICE_NAME', 'com.vibe-seller.server')
        assert al.supervisor() == 'launchd'

    def test_a_gui_terminal_session_is_NOT_supervised(self, monkeypatch):
        """`XPC_SERVICE_NAME` is set for anything launched out of a GUI
        app — Terminal, iTerm, VS Code — and inherited by every shell and
        tmux pane under it. Reading it as "supervised" makes the update
        helper stop the server and wait for a relaunch that will never
        come."""
        for value in (
            'application.com.apple.Terminal.1152921500311871234.1152921500311871239',
            'application.com.googlecode.iTerm2.99.99',
            'application.com.microsoft.VSCode.1.2',
        ):
            monkeypatch.setenv('XPC_SERVICE_NAME', value)
            assert al.supervisor() is None, value

    def test_empty_and_zero_mean_unsupervised(self, monkeypatch):
        for value in ('', '0', '  '):
            monkeypatch.setenv('XPC_SERVICE_NAME', value)
            assert al.supervisor() is None, repr(value)

    def test_the_handoff_carries_the_answer(
        self, bundle_home, monkeypatch, tmp_path
    ):
        """The helper runs in a new process and cannot re-derive this:
        the decision has to travel in the argument document."""
        monkeypatch.setenv('XPC_SERVICE_NAME', 'com.vibe-seller.server')
        monkeypatch.setattr(pu, 'updates_dir', lambda: tmp_path / 'u')
        monkeypatch.setattr(pu, 'apply_in_progress', lambda: None)
        monkeypatch.setattr(al, '_popen_detached', lambda argv, log: object())
        al.apply('0.3.2', pu.TRIGGER_MANUAL, pu.PATH_PIP)
        args = json.loads(
            (tmp_path / 'u' / al.HELPER_ARGS_NAME).read_text('utf-8')
        )
        assert args['supervisor'] == 'launchd'
        assert args['lock'].endswith('apply.lock')


class TestServerPort:
    """The port the helper is told about is the port this server serves.

    The argument document is the helper's whole world: it runs `stop`,
    `start` and `/api/version` against the number written here. Reading
    the configured default instead of the live socket means an update
    that stops and starts a server belonging to something else, and a
    health check that can never pass on any install started with
    `--port` — which is every test instance, `start.sh` on a busy port,
    and the Windows CI legs.
    """

    def test_the_live_listener_wins_over_the_default(self, monkeypatch):
        monkeypatch.setattr(al, 'listening_ports', lambda: [7791])
        monkeypatch.setattr(pu.sys, 'orig_argv', ['python', '-m', 'uvicorn'])
        assert al._server_port() == 7791

    def test_the_command_line_is_the_next_answer(self, monkeypatch):
        monkeypatch.setattr(al, 'listening_ports', lambda: [])
        monkeypatch.setattr(
            pu.sys,
            'orig_argv',
            [
                '/x/python',
                '-m',
                'uvicorn',
                'app.main:app',
                '--host',
                '0.0.0.0',
                '--port',
                '7792',
            ],
        )
        assert al._server_port() == 7792

    def test_the_equals_form_is_read_too(self, monkeypatch):
        monkeypatch.setattr(al, 'listening_ports', lambda: [])
        monkeypatch.setattr(
            pu.sys, 'orig_argv', ['python', '-m', 'uvicorn', '--port=7793']
        )
        assert al._server_port() == 7793

    def test_one_port_on_two_families_is_still_one_port(self, monkeypatch):
        """IPv4 and IPv6 listeners share the number; that is not an
        ambiguity, and treating it as one would put a working install
        back on the configured default."""
        monkeypatch.setattr(al, 'listening_ports', lambda: [7791])
        monkeypatch.setattr(
            pu.sys, 'orig_argv', ['python', '-m', 'uvicorn', '--port', '1']
        )
        assert al._server_port() == 7791

    def test_two_different_listeners_fall_back_to_the_config(self, monkeypatch):
        monkeypatch.setattr(al, 'listening_ports', lambda: [7791, 8123])
        monkeypatch.setattr(pu.sys, 'orig_argv', ['python', '-m', 'uvicorn'])
        import app.config as config  # noqa: PLC0415

        assert al._server_port() == int(config.BACKEND_PORT)

    def test_garbage_on_the_command_line_is_not_a_port(self, monkeypatch):
        monkeypatch.setattr(al, 'listening_ports', lambda: [])
        monkeypatch.setattr(
            pu.sys, 'orig_argv', ['python', '-m', 'uvicorn', '--port', 'soon']
        )
        import app.config as config  # noqa: PLC0415

        assert al._server_port() == int(config.BACKEND_PORT)

    def test_the_apply_document_carries_the_real_port(
        self, monkeypatch, tmp_path
    ):
        """The regression the Linux end-to-end found: a leg on 7791, an
        apply document saying 7777, and a helper that stopped nothing,
        probed nothing, declared the update failed, rolled back, and
        started the old version on the port it had just invented."""
        monkeypatch.setattr(al, 'listening_ports', lambda: [7791])
        monkeypatch.setattr(pu.sys, 'platform', 'linux')
        monkeypatch.setattr(pu, 'updates_dir', lambda: tmp_path)
        args = al.apply_args('99.0.2', pu.TRIGGER_AUTO_IDLE, pu.PATH_PIP)
        assert args['port'] == 7791
        assert args['env']['VIBE_SELLER_PORT'] == '7791'
        assert args['stop_command'][-2:] == ['--port', '7791']
        assert args['start_command'][-2:] == ['--port', '7791']
