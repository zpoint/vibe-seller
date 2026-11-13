"""Tests for the detached apply helper.

This module decides what a failed update LEAVES BEHIND, so these tests
are about ordering and about ``last.json``, not about return values. Only
the process boundaries are faked (``run``, ``popen``,
``version_answered``, ``venv_processes``) — through a small simulation of
the machine, so "the server answered v0.3.2" is a consequence of what the
helper actually ran rather than a scripted line.
"""

from pathlib import Path
import re

import pytest

from app import pip_update
import app.pip_apply_helper as helper

pytestmark = pytest.mark.unit

FREEZE = 'fastapi==0.115.0\nvibe-seller==0.1.0\nplaywright==1.65.1\n'
OLD = '0.1.0'
NEW = '0.3.2'


class Machine:
    """A bundle-shaped install that answers like one.

    ``installed`` is what is on disk and ``serving`` what /api/version
    answers (None = the app is down). Commands move them, so the helper's
    stop/start and its rollback are judged on their actual effect.
    """

    def __init__(self, tmp_path: Path, platform: str):
        self.platform = platform
        self.app_dir = tmp_path / 'app'
        (self.app_dir / '.venv' / 'Scripts').mkdir(parents=True)
        (self.app_dir / 'wheels').mkdir()
        (self.app_dir / 'tray.py').write_text('# tray\n', encoding='utf-8')
        self.updates = tmp_path / 'home' / 'updates'
        self.updates.mkdir(parents=True)
        # ``_isolated`` gives every test a private data root; the lock
        # path the helper takes comes from the argument document.
        self.installed = OLD
        self.serving: str | None = OLD
        #: Can the version the update installs actually boot? False is
        #: the fabricated S5 release.
        self.boots = True
        #: macOS/Linux: ``app/upgrade_restart.py`` moves a running server
        #: onto the new install by itself. False = it did not (a live task
        #: holding the drain, or a supervisor that owns the process).
        self.self_restart = platform != 'win32'
        #: Substrings; a command containing one "fails".
        self.fails: list[str] = []
        self.runs: list[list[str]] = []
        self.starts: list[list[str]] = []
        #: PIDs ``venv_processes`` reports — Windows refuses to install
        #: while any remain.
        self.holders: list[int] = []
        #: The version a rollback command named, set by ``_is_rollback``.
        self.rolled_back_to = OLD
        #: A launchd/systemd supervisor relaunches what we stopped. The
        #: helper must not ALSO start one (review 2 MUST 2), so tests
        #: model the supervisor as: stopping the server brings it back.
        self.start_when_stopped = False
        install = (
            [
                str(self.app_dir / 'uv.exe'),
                'pip',
                'install',
                '--offline',
                f'vibe-seller=={NEW}',
            ]
            if platform == 'win32'
            else [str(tmp_path / 'bin' / 'vibe-seller'), 'upgrade']
        )
        self.args = {
            'platform': platform,
            'path': pip_update.PATH_PIP,
            'app_dir': str(self.app_dir),
            'uv': str(self.app_dir / 'uv.exe'),
            'venv_python': str(
                self.app_dir / '.venv' / 'Scripts' / 'python.exe'
            ),
            'updates_dir': str(self.updates),
            'record': str(self.updates / 'last.json'),
            'install_command': install,
            'stop_command': ['vibe-seller', 'stop', '--port', '7792'],
            'start_command': ['vibe-seller', 'start', '--port', '7792'],
            'tray_python': str(
                self.app_dir / '.venv' / 'Scripts' / 'pythonw.exe'
            ),
            'port': 7792,
            'from_version': OLD,
            'target_version': NEW,
            'trigger': pip_update.TRIGGER_AUTO_IDLE,
            'poll_seconds': 0.0,
            # Who brings the server back. Tests default to unsupervised
            # (the plain `vibe-seller start` daemon); TestSupervised
            # flips it.
            'supervisor': None,
            'lock': str(tmp_path / 'home' / 'updates' / 'apply.lock'),
            'env': {'VIBE_HOME': str(tmp_path / 'home')},
        }

    # ── the patched boundaries ──────────────────────────────────

    #: What ``uv pip freeze`` would report for ``installed`` right now.
    def freeze_text(self) -> str:
        return (
            'fastapi==0.115.0\n'
            f'playwright==1.65.1\nvibe-seller=={self.installed}\n'
        )

    def run(self, argv, timeout=0.0, env=None):  # noqa: ANN001, ARG002
        argv = [str(a) for a in argv]
        joined = ' '.join(argv)
        self.runs.append(argv)
        if any(needle in joined for needle in self.fails):
            return self._done(argv, 1)
        if 'freeze' in argv:
            return self._done(argv, 0, out=self.freeze_text())
        if 'stop' in argv:
            self.serving = None
            if self.start_when_stopped and (
                self.installed == OLD or self.boots
            ):
                self.serving = self.installed
        elif self._is_install(joined):
            self.installed = NEW
            if self.self_restart and self.serving is not None and self.boots:
                self.serving = NEW
        elif self._is_rollback(joined):
            self.installed = self.rolled_back_to
        return self._done(argv, 0)

    def popen(self, argv, env=None):  # noqa: ANN001, ARG002
        argv = [str(a) for a in argv]
        self.starts.append(argv)
        # A rollback target always boots; only the new version is fragile.
        if self.installed != self.args['target_version'] or self.boots:
            self.serving = self.installed
        return object()

    def venv_processes(self, app_dir):  # noqa: ANN001, ARG002
        return list(self.holders)

    def version_answered(self, args):  # noqa: ANN001
        return self.serving or ''

    def _is_install(self, joined: str) -> bool:
        return f'vibe-seller=={NEW}' in joined or joined.rstrip().endswith(
            'upgrade'
        )

    def _is_rollback(self, joined: str) -> bool:
        """A rollback reinstalls a NAMED older version — and the version
        it names is what the machine must then serve. Hard-coding the
        first hop's OLD here is what hid the second-hop bug (review 2
        MUST 1), so this reads the answer out of the command."""
        if ' -r ' in f'{joined} ':
            path = joined.split(' -r ', 1)[1].split()[0]
            for line in Path(path).read_text(encoding='utf-8').splitlines():
                if line.startswith('vibe-seller=='):
                    self.rolled_back_to = line.split('==')[1]
                    return True
            return False
        if 'tool install' in joined:
            found = [
                part
                for part in joined.split()
                if part.startswith('vibe-seller==')
            ]
            if found:
                self.rolled_back_to = found[0].split('==')[1]
                return True
        return False

    @staticmethod
    def _done(argv, code, out='', err=''):  # noqa: ANN001
        return helper.subprocess.CompletedProcess(argv, code, out, err)

    # ── assertions ──────────────────────────────────────────────

    def index_of(self, needle: str) -> int:
        for i, argv in enumerate(self.runs):
            if needle in ' '.join(argv):
                return i
        raise AssertionError(f'no command contained {needle!r}: {self.runs}')

    def ran(self, needle: str) -> bool:
        try:
            self.index_of(needle)
        except AssertionError:
            return False
        return True

    def command(self, needle: str) -> str:
        return ' '.join(self.runs[self.index_of(needle)])

    @property
    def freeze_file(self) -> Path:
        return self.updates / 'freeze-before.txt'


def _machine(tmp_path, monkeypatch, platform):  # noqa: ANN001 — fixture body
    m = Machine(tmp_path, platform)
    # Shrink every wait in the module: what these tests pin is the ORDER
    # of operations and the record, not real seconds.
    for name in (
        'HEALTH_TIMEOUT',
        'SELF_RESTART_GRACE',
        'RECOVERY_PROBE',
        'RELEASE_TIMEOUT',
    ):
        monkeypatch.setattr(helper, name, 0.05)
    monkeypatch.setattr(helper, 'run', m.run)
    monkeypatch.setattr(helper, 'popen', m.popen)
    monkeypatch.setattr(helper, 'venv_processes', m.venv_processes)
    monkeypatch.setattr(helper, 'version_answered', m.version_answered)
    return m


@pytest.fixture(name='win')
def _win_fixture(tmp_path, monkeypatch):
    return _machine(tmp_path, monkeypatch, 'win32')


@pytest.fixture(name='posix')
def _posix_fixture(tmp_path, monkeypatch):
    return _machine(tmp_path, monkeypatch, 'linux')


class TestHappyPath:
    def test_windows_stops_everything_before_installing(self, win):
        """A running process locks the venv's files on Windows, so the
        install cannot start until nothing runs from it."""
        rec = helper.apply_update(win.args)
        assert rec['status'] == 'ok'
        assert win.index_of('stop --port') < win.index_of('pip install')
        assert win.serving == NEW
        # The tray owns the server, so the tray is what gets started —
        # the same thing the installer's silent finish does.
        assert 'tray.py' in ' '.join(win.starts[0])
        assert win.starts[0][0].endswith('pythonw.exe')

    def test_posix_installs_first_and_lets_the_server_restart_itself(
        self, posix
    ):
        """``app/upgrade_restart.py`` moves a running server onto the new
        install within seconds; the helper must not fight it with an
        unnecessary stop/start when that already happened."""
        rec = helper.apply_update(posix.args)
        assert rec['status'] == 'ok'
        assert posix.command('upgrade')
        assert not posix.ran('stop --port')
        assert posix.starts == []

    def test_the_record_says_who_asked_and_which_path(self, win):
        rec = helper.apply_update(win.args)
        assert rec['trigger'] == pip_update.TRIGGER_AUTO_IDLE
        assert rec['path'] == pip_update.PATH_PIP
        assert (rec['from'], rec['to']) == (OLD, NEW)
        assert rec['stage'] is None and rec['error'] is None
        assert rec['reported'] is False

    def test_the_posix_helper_starts_the_server_when_self_restart_did_not(
        self, posix
    ):
        """The self-restart is a hope, not an assumption: launchd, a
        systemd unit and a plain daemon each behave differently, so the
        fallback is to bring it up here."""
        posix.self_restart = False
        rec = helper.apply_update(posix.args)
        assert rec['status'] == 'ok'
        assert posix.ran('stop --port')
        assert posix.starts[0] == ['vibe-seller', 'start', '--port', '7792']
        assert posix.serving == NEW

    def test_windows_starts_the_tray_after_the_health_check_passes(self, win):
        """Ordering matters: starting the tray is how the new version gets
        a chance to answer, and the record must not say ok before it did."""
        win.boots = False
        rec = helper.apply_update(win.args)
        assert rec['status'] != 'ok'
        assert win.starts


class TestNeverStopsServing:
    """The invariant every other test here protects: an apply that fails
    ends with the OLD version answering."""

    def test_a_freeze_failure_leaves_the_server_alone(self, win):
        win.fails.append('freeze')
        rec = helper.apply_update(win.args)
        assert rec['status'] == 'failed'
        assert rec['stage'] == helper.STAGE_FREEZE
        assert not win.ran('--offline'), 'installed after a failed freeze'
        assert not win.ran('stop --port'), 'stopped the server needlessly'
        assert win.starts == []
        assert win.serving == OLD

    def test_a_locked_venv_aborts_before_anything_is_installed(self, win):
        """The stop step gives up rather than installing onto locked
        files, which is how a half-replaced venv happens."""
        win.holders = [4242]
        win.fails.append('taskkill')
        rec = helper.apply_update(win.args)
        assert rec['status'] == 'failed'
        assert rec['stage'] == helper.STAGE_STOP
        assert not win.ran('--offline')

    def test_an_install_failure_rolls_back_from_the_bundles_own_wheels(
        self, win
    ):
        win.fails.append(f'vibe-seller=={NEW}')
        rec = helper.apply_update(win.args)
        assert rec['status'] == 'rolled_back'
        assert rec['stage'] == helper.STAGE_ROLLBACK
        assert 'the install failed' in rec['error']
        assert win.freeze_file.exists()
        argv = win.command('-r')
        assert str(win.freeze_file) in argv
        # Offline, from the cache AND the bundle's wheels: a recovery
        # path must not need the network the failed update may not have,
        # and `--no-index` alone could not name a pip-installed version.
        assert '--offline' in argv
        assert '--no-index' not in argv
        assert str(win.app_dir / 'wheels') in argv
        assert 'tray.py' in ' '.join(win.starts[-1])
        assert win.serving == OLD

    def test_a_release_that_will_not_boot_is_replaced_by_the_old_one(self, win):
        """S5: a fabricated release that dies on import must leave the
        OLD version serving, never a dark machine."""
        win.boots = False
        rec = helper.apply_update(win.args)
        assert rec['status'] == 'rolled_back'
        assert f'never reported {NEW}' in rec['error']
        assert win.serving == OLD

    def test_the_same_on_posix(self, posix):
        posix.boots = False
        rec = helper.apply_update(posix.args)
        assert rec['status'] == 'rolled_back'
        assert posix.serving == OLD
        assert posix.ran(f'vibe-seller=={OLD}')

    def test_posix_rollback_prefers_the_cache(self, posix):
        posix.boots = False
        rec = helper.apply_update(posix.args)
        assert rec['status'] == 'rolled_back'
        argv = posix.command(f'vibe-seller=={OLD}')
        assert 'tool install' in argv
        assert '--offline' in argv

    def test_posix_rollback_falls_back_to_the_index_when_the_cache_is_cold(
        self, posix
    ):
        """Offline first, but a recovery that needs the network still
        beats leaving a broken version installed."""
        posix.boots = False
        posix.fails.append('--offline')
        rec = helper.apply_update(posix.args)
        assert rec['status'] == 'rolled_back'
        reinstalls = [
            ' '.join(c) for c in posix.runs if 'tool install' in ' '.join(c)
        ]
        assert len(reinstalls) == 2, reinstalls
        assert '--offline' in reinstalls[0]
        assert '--offline' not in reinstalls[1]
        assert posix.serving == OLD

    def test_a_rollback_that_also_fails_is_reported_as_a_failure(self, win):
        """Honesty over optimism: "rolled back" is only said when the old
        version was actually put back."""
        win.fails.append(f'install --offline vibe-seller=={NEW}')
        win.fails.append('-r')
        rec = helper.apply_update(win.args)
        assert rec['status'] == 'failed'
        assert 'rollback failed' in rec['error']
        assert rec['error_class'].endswith('+ApplyError')

    def test_an_attempt_is_enough_to_warrant_a_rollback(self, win):
        """A half-run install leaves a venv that may not import at all, so
        the rollback follows the ATTEMPT. Gating it on success would boot
        a machine with a torn venv."""
        win.fails.append('vibe-seller')
        helper.apply_update(win.args)
        assert win.ran('-r'), 'no rollback after a failed install attempt'


class TestRecord:
    def test_the_status_words_match_what_the_server_reads(self):
        """The helper cannot import the package (it outlives it), so both
        sides spell the same words — and drift here would silently turn a
        rollback into an unreported event."""
        assert pip_update.ROLLED_BACK == helper.STATUS_ROLLED_BACK
        assert pip_update.STATUS_OK == helper.STATUS_OK
        assert pip_update.STATUS_FAILED == helper.STATUS_FAILED

    def test_every_recorded_stage_is_from_the_vocabulary(self, win):
        stages = {
            getattr(helper, name)
            for name in dir(helper)
            if name.startswith('STAGE_')
        }
        win.fails.append('freeze')
        assert helper.apply_update(win.args)['stage'] in stages
        win.fails = ['taskkill']
        win.holders = [1]
        assert helper.apply_update(win.args)['stage'] in stages

    def test_the_record_is_replaced_not_appended(self, win):
        helper.apply_update(win.args)
        first = (win.updates / 'last.json').read_text('utf-8')
        win.fails.append('freeze')
        helper.apply_update(win.args)
        assert (win.updates / 'last.json').read_text('utf-8') != first

    def test_main_maps_the_record_to_an_exit_code(self, win, tmp_path):
        import json  # noqa: PLC0415 — test-local

        args_file = tmp_path / 'args.json'
        args_file.write_text(json.dumps(win.args), encoding='utf-8')
        assert helper.main(['pip_apply.py', str(args_file)]) == 0
        win.fails.append('freeze')
        assert helper.main(['pip_apply.py', str(args_file)]) == 1

    def test_no_arguments_is_a_usage_error(self):
        assert helper.main(['pip_apply.py']) == 2


class TestStandalone:
    """The helper runs from a copy under ``<VIBE_HOME>/updates/`` AFTER
    the package it shipped in has been replaced, so it may not import
    anything from that package."""

    #: All it may reach for: the standard library.
    ALLOWED = {
        'json',
        'os',
        'pathlib',
        'subprocess',
        'sys',
        'time',
        'traceback',
        'urllib',
        '__future__',
    }

    def test_it_imports_only_the_standard_library(self):
        source = Path(helper.__file__).read_text(encoding='utf-8')
        imported = {
            match.group(1).split('.')[0]
            for match in re.finditer(
                r'^(?:from|import)\s+([A-Za-z_][\w.]*)', source, re.M
            )
        }
        assert imported <= self.ALLOWED, imported - self.ALLOWED

    def test_a_grace_of_zero_does_not_mean_the_default(self, win):
        """``grace = 0`` on Windows means "nothing is running to wait for,
        stop and start it yourself". A ``timeout or DEFAULT`` idiom would
        turn it back into two minutes of waiting on a dead port."""
        win.serving = None
        assert helper.wait_for_version(win.args, NEW, 0.0) is False


class TestSecondHop:
    """S5b: the rollback must work for EVERY hop, not just the first.

    The bug review 2 caught: ``{app}\\wheels`` only ever holds the
    version the INSTALLER shipped, so a rollback that is ``--no-index``
    against it cannot name 0.3.2 — the second failed update would leave
    a half-installed venv and a dark machine, which is the one outcome
    this module exists to prevent.
    """

    def test_the_rollback_is_offline_and_reads_the_bundle(self, win):
        """The command, not just the intent: cache + the bundle's wheels,
        with no index and no network."""
        win.fails.append(f'vibe-seller=={NEW}')
        helper.apply_update(win.args)
        argv = win.command('-r')
        assert '--offline' in argv, 'a rollback that needs the network'
        assert '--no-index' not in argv, 'cannot name a pip-installed version'
        assert str(win.app_dir / 'wheels') in argv

    def test_old_to_v2_to_a_broken_v3_lands_on_v2(self, win):
        """Two applies in a row, the second one failing. The machine ends
        on v2 — the version that was actually serving — not on 0.1.0,
        which it has not run for a release."""
        first = helper.apply_update(win.args)
        assert first['status'] == 'ok'
        assert win.installed == NEW

        # Now 0.3.2 -> 0.3.3, and 0.3.3 will not boot.
        win.args['from_version'] = NEW
        win.args['target_version'] = '0.3.3'
        win.args['install_command'] = [
            str(win.app_dir / 'uv.exe'),
            'pip',
            'install',
            '--offline',
            'vibe-seller==0.3.3',
        ]
        win.boots = False
        second = helper.apply_update(win.args)
        assert second['status'] == 'rolled_back'
        assert (second['from'], second['to']) == (NEW, '0.3.3')
        assert win.installed == NEW, 'the rollback named the wrong version'
        assert win.serving == NEW, f'serving {win.serving}'
        argv = win.command('-r')
        assert (
            f'vibe-seller=={NEW}'
            in open(win.freeze_file, encoding='utf-8').read()
        ), 'the freeze lost the version actually installed'
        del argv

    def test_the_same_two_hops_on_posix(self, posix):
        posix.self_restart = False
        assert helper.apply_update(posix.args)['status'] == 'ok'
        posix.args['from_version'] = NEW
        posix.args['target_version'] = '0.3.3'
        posix.boots = False
        rec = helper.apply_update(posix.args)
        assert rec['status'] == 'rolled_back'
        assert posix.ran(f'vibe-seller=={NEW}'), (
            'rolled back to the wrong version'
        )
        assert posix.serving == NEW


class TestSupervised:
    """A server under launchd/systemd is restarted by its supervisor.
    Starting our own copy there puts two servers on one port, outside the
    unit that owns it — so in supervised mode the helper only stops."""

    def test_an_unsupervised_server_is_started_by_the_helper(self, posix):
        posix.self_restart = False
        assert helper.apply_update(posix.args)['status'] == 'ok'
        assert posix.starts == [['vibe-seller', 'start', '--port', '7792']]

    def test_a_supervised_server_is_never_started_twice(self, posix):
        posix.self_restart = False
        posix.args['supervisor'] = 'launchd'
        # Nothing answers until the supervisor relaunches it, which the
        # simulation models as: the start we DO NOT do is what the
        # supervisor does instead.
        posix.start_when_stopped = True
        rec = helper.apply_update(posix.args)
        assert rec['status'] == 'ok', rec
        assert posix.starts == [], 'started a second server under launchd'
        assert posix.ran('stop --port'), 'never asked the supervisor to restart'

    def test_a_supervised_rollback_also_leaves_it_to_the_supervisor(
        self, posix
    ):
        posix.self_restart = False
        posix.boots = False
        posix.args['supervisor'] = 'systemd'
        posix.start_when_stopped = True
        rec = helper.apply_update(posix.args)
        assert rec['status'] == 'rolled_back'
        assert posix.starts == []
        assert posix.serving == OLD

    def test_windows_ignores_the_supervisor_flag(self, win):
        """There is no service manager here — the tray owns the server,
        and starting the tray IS the restart."""
        win.args['supervisor'] = 'systemd'
        assert helper.apply_update(win.args)['status'] == 'ok'
        assert 'tray.py' in ' '.join(win.starts[0])


class TestSingleFlight:
    """One apply at a time, machine-wide: two servers can share one
    VIBE_HOME, and an auto-apply can race an admin's click."""

    def test_the_lock_is_held_for_the_whole_run(self, win):
        helper.acquire_lock(win.args['lock'])
        try:
            with pytest.raises(helper.AnotherApplyInProgress):
                helper.acquire_lock(win.args['lock'])
        finally:
            helper._release_lock()

    def test_a_second_apply_records_and_does_not_touch_anything(
        self, win, capsys
    ):
        import json  # noqa: PLC0415 — test-local

        helper.acquire_lock(win.args['lock'])
        try:
            code = helper.main(['pip_apply.py', self._args_file(win, json)])
        finally:
            helper._release_lock()
        assert code == 1
        assert not win.runs, 'the loser still ran commands'
        rec = json.loads(
            (win.updates / 'last.json').read_text(encoding='utf-8')
        )
        assert rec['status'] == 'failed' and rec['stage'] == 'lock'

    @staticmethod
    def _args_file(win, json):
        path = win.updates / 'args.json'
        path.write_text(json.dumps(win.args), encoding='utf-8')
        return str(path)

    def test_the_lock_is_released_with_the_process(self, win):
        """No stale-lock timer: the OS drops an advisory lock when the
        holder exits, so a killed helper cannot wedge updates forever."""
        helper.acquire_lock(win.args['lock'])
        helper._release_lock()
        helper.acquire_lock(win.args['lock'])
        helper._release_lock()
