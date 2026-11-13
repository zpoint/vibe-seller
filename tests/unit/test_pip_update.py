"""Tests for the pip path's mechanics: what commands get built, and what
they are allowed to touch.

The two hazards this module exists to make impossible are an update that
applies itself during the *prefetch* (#244 found that live) and an apply
that needs the network at 03:00, so the tests are about argument
construction and about which interpreter reaches which install.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app import bundle
import app.pip_update as pu

pytestmark = pytest.mark.unit


def _done(code=0, out='', err=''):
    return pu.subprocess.CompletedProcess(['x'], code, out, err)


@pytest.fixture(name='bundle_home')
def _bundle_home(tmp_path, monkeypatch):
    """A Windows-shaped installer bundle: {app} with .venv, python, uv."""
    app = tmp_path / 'app'
    venv = app / '.venv'
    (venv / 'Scripts').mkdir(parents=True)
    (app / 'python').mkdir()
    (app / 'python' / 'python.exe').write_text('')
    # The wheel set the installer laid down: a second LOCAL source both
    # the apply and its rollback may resolve from.
    (app / 'wheels').mkdir()
    (app / 'uv.exe').write_text('')
    (app / 'unins000.exe').write_text('')
    monkeypatch.setattr(pu.sys, 'platform', 'win32')
    monkeypatch.setattr(pu.sys, 'prefix', str(venv))
    monkeypatch.setattr(
        pu.sys, 'executable', str(venv / 'Scripts' / 'python.exe')
    )
    monkeypatch.setattr(
        pu.sys, '_base_executable', str(app / 'python' / 'python.exe')
    )
    monkeypatch.setattr(bundle, 'app_root', lambda: app)
    return app


class TestApplyCommand:
    def test_the_gate_and_the_apply_differ_by_one_flag(self, bundle_home):
        """The whole safety of "ready means it will work offline" rests
        on the dry run being THE SAME command. Two builders would drift
        within a release."""
        real = pu.apply_command('0.3.2')
        dry = pu.apply_command('0.3.2', dry_run=True)
        assert dry.count('--dry-run') == 1
        assert [a for a in dry if a != '--dry-run'] == real

    def test_the_apply_is_offline_and_pins_the_target(self, bundle_home):
        argv = pu.apply_command('0.3.2')
        assert '--offline' in argv
        assert '--no-index' not in argv
        assert argv[-1] == 'vibe-seller==0.3.2'
        assert '--reinstall-package' in argv
        # The bundle's wheel set is a second LOCAL source, so a package
        # the tray needs (and the wheel does not declare) resolves the
        # same way BuildRuntimeEnv installed it.
        assert '--find-links' in argv
        assert argv[argv.index('--find-links') + 1] == str(
            bundle_home / 'wheels'
        )
        # It installs into THIS venv, named explicitly — never whatever
        # `uv` decides is the default environment.
        assert argv[argv.index('--python') + 1] == pu.sys.executable

    def test_the_bundle_uv_does_it_not_one_from_path(self, bundle_home):
        assert pu.uv_binary() == str(bundle_home / 'uv.exe')
        assert pu.apply_command('0.3.2')[0] == str(bundle_home / 'uv.exe')

    def test_a_missing_bundled_uv_closes_the_pip_path(self, bundle_home):
        """Without {app}\\uv.exe there is no way to know which resolver
        would run, so there is no pip path at all. The command it would
        have run still names the bundle's uv, never a uv off PATH that
        could resolve differently."""
        (bundle_home / 'uv.exe').unlink()
        assert pu.available() is False
        assert pu.apply_command('0.3.2')[0] == str(bundle_home / 'uv.exe')


class TestPrefetchInterpreter:
    """Regression for the bug #244 found live: `uv tool run --python <the
    tool venv's own python>` installs INTO that venv, so the prefetch
    moved the running tool to the target and the restart watcher applied
    an update nobody asked for."""

    def test_windows_uses_the_bundled_base_python(self, bundle_home):
        assert pu.prefetch_python() == str(
            bundle_home / 'python' / 'python.exe'
        )
        assert pu.prefetch_python() != pu.sys.executable

    def test_a_missing_base_python_refuses_to_guess(self, bundle_home):
        (bundle_home / 'python' / 'python.exe').unlink()
        with pytest.raises(pu.PrefetchError, match='base python is missing'):
            pu.prefetch_python()

    def test_unix_never_hands_over_the_tool_venv_interpreter(self, monkeypatch):
        monkeypatch.setattr(pu.sys, 'platform', 'linux')
        chosen = pu.prefetch_python()
        assert chosen != pu.sys.executable
        assert not Path(chosen).is_relative_to(Path(pu.sys.prefix))

    def test_unix_prefers_the_base_executable_outside_the_venv(
        self, monkeypatch, tmp_path
    ):
        prefix = tmp_path / 'toolvenv'
        prefix.mkdir()
        real = tmp_path / 'uv-python'
        real.write_text('')
        monkeypatch.setattr(pu.sys, 'platform', 'linux')
        monkeypatch.setattr(pu.sys, 'prefix', str(prefix))
        monkeypatch.setattr(pu.sys, '_base_executable', str(real))
        assert pu.prefetch_python() == str(real.resolve())

    def test_unix_finds_a_bare_python3_when_the_versioned_name_is_absent(
        self, monkeypatch, tmp_path
    ):
        prefix = tmp_path / 'toolvenv'
        prefix.mkdir()
        base = tmp_path / 'basepy' / 'bin'
        base.mkdir(parents=True)
        (base / 'python3').write_text('')
        monkeypatch.setattr(pu.sys, 'platform', 'linux')
        monkeypatch.setattr(pu.sys, 'prefix', str(prefix))
        monkeypatch.setattr(pu.sys, 'base_prefix', str(tmp_path / 'basepy'))
        monkeypatch.delattr(pu.sys, '_base_executable', raising=True)
        assert pu.prefetch_python() == str((base / 'python3').resolve())

    def test_unix_fails_closed_with_nothing_outside_the_venv(
        self, monkeypatch, tmp_path
    ):
        prefix = tmp_path / 'venv'
        bin_dir = prefix / 'bin'
        bin_dir.mkdir(parents=True)
        (bin_dir / 'python3').write_text('')
        monkeypatch.setattr(pu.sys, 'platform', 'linux')
        monkeypatch.setattr(pu.sys, 'prefix', str(prefix))
        monkeypatch.setattr(pu.sys, 'base_prefix', str(prefix))
        monkeypatch.setattr(
            pu.sys, '_base_executable', str(bin_dir / 'python3')
        )
        with pytest.raises(pu.PrefetchError, match='refusing to use'):
            pu.prefetch_python()


class TestWarm:
    def test_the_pin_resolves_in_an_ephemeral_env(self, monkeypatch):
        ran = []
        monkeypatch.setattr(pu.sys, 'platform', 'linux')
        monkeypatch.setattr(
            pu, '_run', lambda argv, timeout: ran.append(argv) or _done()
        )
        monkeypatch.setattr(
            pu.upgrade_restart, 'installed_version', lambda: '0.8.0'
        )
        monkeypatch.setattr(pu, 'get_version', lambda: '0.8.0')
        pu.warm('0.9.0')
        assert ran[0][:3] == [pu.uv_binary(), 'tool', 'run']
        assert ran[0][ran[0].index('--from') + 1] == 'vibe-seller==0.9.0'
        assert ran[0][ran[0].index('--python') + 1] == pu.prefetch_python()
        assert ran[0][-2:] == ['vibe-seller', '--version']

    def test_a_fetch_failure_names_the_version(self, monkeypatch):
        monkeypatch.setattr(pu.sys, 'platform', 'linux')
        monkeypatch.setattr(
            pu, '_run', lambda argv, timeout: _done(1, err='no such version')
        )
        with pytest.raises(pu.PrefetchError, match='0.9.0'):
            pu.warm('0.9.0')

    def test_a_prefetch_that_moved_the_install_is_fatal(self, monkeypatch):
        """The whole point of the interpreter rules above, stated as a
        check rather than only as a construction."""
        monkeypatch.setattr(pu.sys, 'platform', 'linux')
        monkeypatch.setattr(pu, '_run', lambda argv, timeout: _done())
        monkeypatch.setattr(
            pu.upgrade_restart, 'installed_version', lambda: '0.9.0'
        )
        monkeypatch.setattr(pu, 'get_version', lambda: '0.8.0')
        with pytest.raises(pu.PrefetchError, match='never apply itself'):
            pu.warm('0.9.0')


class TestBrowser:
    def test_posix_installs_the_targets_browser_from_its_own_env(
        self, monkeypatch
    ):
        """The Chromium revision is fixed by the TARGET's Playwright, so
        the target's CLI is what has to fetch it — and doing it here is
        what keeps the restart free of a 150 MB download."""
        ran = []
        monkeypatch.setattr(pu.sys, 'platform', 'linux')
        monkeypatch.setattr(
            pu, '_run', lambda argv, timeout: ran.append(argv) or _done()
        )
        monkeypatch.setattr(
            pu.upgrade_restart, 'installed_version', lambda: '0.8.0'
        )
        monkeypatch.setattr(pu, 'get_version', lambda: '0.8.0')
        pu.pre_install_browser('0.9.0')
        assert ran[0][-2:] == ['vibe-seller', 'install-browser']
        assert ran[0][ran[0].index('--from') + 1] == 'vibe-seller==0.9.0'

    def test_a_failed_browser_fetch_does_not_fail_the_update(
        self, monkeypatch, caplog
    ):
        """The server retries the browser at startup, so this is only a
        slower restart — never a reason to refuse the update."""
        monkeypatch.setattr(pu.sys, 'platform', 'linux')
        monkeypatch.setattr(
            pu, '_run', lambda argv, timeout: _done(1, err='cdn down')
        )
        monkeypatch.setattr(
            pu.upgrade_restart, 'installed_version', lambda: '0.8.0'
        )
        monkeypatch.setattr(pu, 'get_version', lambda: '0.8.0')
        pu.pre_install_browser('0.9.0')

    def test_windows_needs_no_browser_fetch(self, bundle_home, monkeypatch):
        """The browser is part of the bundle, and the fingerprint already
        proved this release did not change it."""
        calls = []
        monkeypatch.setattr(
            pu, '_run', lambda argv, timeout: calls.append(argv)
        )
        pu.pre_install_browser('0.3.2')
        assert calls == []

    def test_rollback_insurance_warms_the_running_version(self, monkeypatch):
        ran = []
        monkeypatch.setattr(pu.sys, 'platform', 'linux')
        monkeypatch.setattr(
            pu, '_run', lambda argv, timeout: ran.append(argv) or _done()
        )
        monkeypatch.setattr(
            pu.upgrade_restart, 'installed_version', lambda: '0.8.0'
        )
        monkeypatch.setattr(pu, 'get_version', lambda: '0.8.0')
        pu.keep_rollback_warm()
        assert ran[0][ran[0].index('--from') + 1] == 'vibe-seller==0.8.0'

    def test_the_insurance_never_breaks_the_cycle(self, monkeypatch):
        monkeypatch.setattr(pu.sys, 'platform', 'linux')
        monkeypatch.setattr(
            pu, '_run', lambda argv, timeout: _done(1, err='cold')
        )
        monkeypatch.setattr(pu, 'get_version', lambda: '0.8.0')
        monkeypatch.setattr(
            pu.upgrade_restart, 'installed_version', lambda: '0.8.0'
        )
        pu.keep_rollback_warm()  # logged, not raised

    def test_the_bundle_wheels_make_warming_pointless(
        self, bundle_home, monkeypatch
    ):
        """Right after an install, ``{app}\\wheels`` still holds the
        version that is running: the rollback can be offline from the
        bundle alone, so there is nothing to fetch."""
        version = '0.1.0'
        monkeypatch.setattr(pu, 'get_version', lambda: version)
        (
            bundle_home / 'wheels' / f'vibe_seller-{version}-py3-none-any.whl'
        ).write_bytes(b'')
        with patch.object(pu, '_run') as run:
            pu.keep_rollback_warm()
        run.assert_not_called()

    def test_after_one_pip_update_the_bundle_no_longer_covers_it(
        self, bundle_home, monkeypatch
    ):
        """Review 2 MUST 1, at the source of the bug: 0.1.0 -> 0.3.2 by
        pip put 0.3.2 in the CACHE and nowhere in ``{app}\\wheels``, so a
        later failed update could only roll back offline if this cycle
        warmed what is running now."""
        monkeypatch.setattr(pu, 'get_version', lambda: '0.3.2')
        (
            bundle_home / 'wheels' / 'vibe_seller-0.1.0-py3-none-any.whl'
        ).write_bytes(b'')
        ran = []
        monkeypatch.setattr(
            pu, '_run', lambda argv, timeout: ran.append(argv) or _done()
        )
        monkeypatch.setattr(
            pu.upgrade_restart, 'installed_version', lambda: '0.3.2'
        )
        pu.keep_rollback_warm()
        assert ran and 'vibe-seller==0.3.2' in ' '.join(ran[0])


class TestOfflineGate:
    def test_a_clean_dry_run_is_a_pass(self, bundle_home, monkeypatch):
        monkeypatch.setattr(pu, '_run', lambda argv, timeout: _done())
        assert pu.offline_apply_proven('0.3.2') is None

    def test_a_cold_cache_says_why(self, bundle_home, monkeypatch):
        monkeypatch.setattr(
            pu, '_run', lambda argv, timeout: _done(1, err='unreachable')
        )
        reason = pu.offline_apply_proven('0.3.2')
        assert reason and 'not be complete' in reason
        assert 'unreachable' in reason

    def test_the_gate_dry_runs_the_apply_command(
        self, bundle_home, monkeypatch
    ):
        seen = []
        monkeypatch.setattr(
            pu, '_run', lambda argv, timeout: seen.append(argv) or _done()
        )
        pu.offline_apply_proven('0.3.2')
        assert seen[0] == pu.apply_command('0.3.2', dry_run=True)


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
        monkeypatch.setattr(pu, '_server_port', lambda: 7792)
        started = []
        monkeypatch.setattr(
            pu, '_popen_detached', lambda argv, log: started.append(argv)
        )
        proc = pu.apply('0.3.2', pu.TRIGGER_AUTO_IDLE, pu.PATH_PIP)
        del proc
        helper = tmp_path / 'home' / 'updates' / pu.HELPER_MODULE
        assert helper.is_file()
        assert helper.read_text(encoding='utf-8') == Path(
            pu.__file__
        ).with_name(pu.HELPER_MODULE).read_text(encoding='utf-8')
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
        monkeypatch.setattr(pu, '_server_port', lambda: 7792)
        monkeypatch.setattr(pu, '_popen_detached', lambda argv, log: object())
        pu.apply('0.3.2', pu.TRIGGER_AUTO_IDLE, pu.PATH_PIP)
        args = json.loads(
            (tmp_path / 'u' / pu.HELPER_ARGS_NAME).read_text(encoding='utf-8')
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
        monkeypatch.setattr(pu, '_server_port', lambda: 7792)
        monkeypatch.setattr(pu, '_popen_detached', lambda argv, log: object())
        pu.apply('0.3.2', pu.TRIGGER_MANUAL, pu.PATH_PIP)
        args = json.loads(
            (tmp_path / 'u' / pu.HELPER_ARGS_NAME).read_text(encoding='utf-8')
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
        monkeypatch.setattr(pu, '_server_port', lambda: 7792)
        monkeypatch.setattr(pu, '_popen_detached', lambda argv, log: object())
        pu.apply('0.9.0', pu.TRIGGER_AUTO_IDLE, pu.PATH_PIP)
        args = json.loads(
            (tmp_path / 'u' / pu.HELPER_ARGS_NAME).read_text(encoding='utf-8')
        )
        assert args['install_command'][-1] == 'upgrade'
        assert args['start_command'][-3:] == ['start', '--port', '7792']
        assert args['stop_command'][-3:] == ['stop', '--port', '7792']


class TestRecord:
    def test_no_record_is_no_opinion(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pu, 'updates_dir', lambda: tmp_path)
        assert pu.read_record() is None
        assert pu.record_mtime() == 0.0

    def test_a_written_record_reads_back(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pu, 'updates_dir', lambda: tmp_path)
        pu.write_record({'status': 'ok', 'to': '0.3.2'})
        assert pu.read_record()['to'] == '0.3.2'
        assert pu.record_mtime() > 0

    def test_marking_reported_is_idempotent_and_persistent(
        self, tmp_path, monkeypatch
    ):
        """One failure is counted once, however many boots it takes to
        recover."""
        monkeypatch.setattr(pu, 'updates_dir', lambda: tmp_path)
        pu.write_record({'status': 'failed', 'reported': False})
        pu.mark_record_reported()
        assert pu.read_record()['reported'] is True
        pu.mark_record_reported()
        assert pu.read_record()['reported'] is True

    def test_a_corrupt_record_is_read_as_absent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pu, 'updates_dir', lambda: tmp_path)
        (tmp_path / 'last.json').write_text('{not json', encoding='utf-8')
        assert pu.read_record() is None

    def test_the_record_words_are_the_helpers(self):
        """app/pip_apply_helper.py repeats them as literals because it
        cannot import this package; this is the seam that pins it."""
        import app.pip_apply_helper as h  # noqa: PLC0415

        assert (pu.ROLLED_BACK, pu.STATUS_OK, pu.STATUS_FAILED) == (
            h.STATUS_ROLLED_BACK,
            h.STATUS_OK,
            h.STATUS_FAILED,
        )


class TestSupervisorDetection:
    """A wrong answer here is a dark machine after a failed update, so
    the false case is the one that matters most."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        for name in ('INVOCATION_ID', 'NOTIFY_SOCKET', 'XPC_SERVICE_NAME'):
            monkeypatch.delenv(name, raising=False)

    def test_a_plain_shell_is_unsupervised(self):
        assert pu.supervisor() is None

    def test_systemd_unit(self, monkeypatch):
        monkeypatch.setenv('INVOCATION_ID', 'a1b2c3')
        assert pu.supervisor() == 'systemd'

    def test_systemd_notify_socket(self, monkeypatch):
        monkeypatch.setenv('NOTIFY_SOCKET', '/run/systemd/notify')
        assert pu.supervisor() == 'systemd'

    def test_a_launchd_job_label_is_supervised(self, monkeypatch):
        # The label docs/macos-setup.md installs.
        monkeypatch.setenv('XPC_SERVICE_NAME', 'com.vibe-seller.server')
        assert pu.supervisor() == 'launchd'

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
            assert pu.supervisor() is None, value

    def test_empty_and_zero_mean_unsupervised(self, monkeypatch):
        for value in ('', '0', '  '):
            monkeypatch.setenv('XPC_SERVICE_NAME', value)
            assert pu.supervisor() is None, repr(value)

    def test_the_handoff_carries_the_answer(
        self, bundle_home, monkeypatch, tmp_path
    ):
        """The helper runs in a new process and cannot re-derive this:
        the decision has to travel in the argument document."""
        monkeypatch.setenv('XPC_SERVICE_NAME', 'com.vibe-seller.server')
        monkeypatch.setattr(pu, 'updates_dir', lambda: tmp_path / 'u')
        monkeypatch.setattr(pu, 'apply_in_progress', lambda: None)
        monkeypatch.setattr(pu, '_popen_detached', lambda argv, log: object())
        pu.apply('0.3.2', pu.TRIGGER_MANUAL, pu.PATH_PIP)
        args = json.loads(
            (tmp_path / 'u' / pu.HELPER_ARGS_NAME).read_text('utf-8')
        )
        assert args['supervisor'] == 'launchd'
        assert args['lock'].endswith('apply.lock')
