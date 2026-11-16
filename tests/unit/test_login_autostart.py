"""Unit tests for ``vibe-seller autostart`` (app/login_autostart.py).

launchctl is never called: a fake runner keeps the "loaded" state, and
every path points at tmp dirs, so these run on any OS.
"""

from pathlib import Path
import plistlib
import subprocess
import sys

import pytest

from app import cli, login_autostart, windows_autostart
from app.login_autostart import MacAgent, build_plist, mac_label

pytestmark = pytest.mark.unit

CLI = '/opt/example/bin/vibe-seller'


class FakeLaunchctl:
    """Records launchctl calls and models bootstrap / bootout / print."""

    def __init__(self, bootstrap_failures: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.loaded = False
        self.bootstrap_failures = bootstrap_failures

    def __call__(self, argv, **_kw):
        assert argv[0] == 'launchctl'
        self.calls.append(argv[1:])
        verb = argv[1]
        rc = 0
        if verb == 'print':
            rc = 0 if self.loaded else 113
        elif verb == 'bootstrap':
            if self.bootstrap_failures:
                self.bootstrap_failures -= 1
                return subprocess.CompletedProcess(
                    argv, 5, '', 'Bootstrap failed: 5: Input/output error'
                )
            self.loaded = True
        elif verb == 'bootout':
            self.loaded = False
        return subprocess.CompletedProcess(argv, rc, '', '')

    def verbs(self) -> list[str]:
        return [c[0] for c in self.calls if c[0] != 'print']


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setattr(login_autostart.time, 'sleep', lambda _s: None)
    fake = FakeLaunchctl()
    a = MacAgent(7777, home=tmp_path, run=fake, uid=501)
    a.fake = fake
    return a


def test_label_is_per_port():
    assert mac_label(7777) == 'com.vibe-seller.start'
    assert mac_label(8080) == 'com.vibe-seller.start.8080'


def test_plist_runs_start_at_login_and_outlives_the_job(tmp_path):
    plist = build_plist(CLI, 7777, tmp_path)
    assert plist['Label'] == 'com.vibe-seller.start'
    assert plist['ProgramArguments'] == [CLI, 'start', '--port', '7777']
    assert plist['RunAtLoad'] is True
    # The daemonised server must survive the launcher job's exit.
    assert plist['AbandonProcessGroup'] is True
    assert 'KeepAlive' not in plist
    env = plist['EnvironmentVariables']
    assert env['HOME'] == str(tmp_path)
    path = env['PATH'].split(':')
    assert path[0] == '/opt/example/bin'
    assert len(path) == len(set(path))
    assert f'{tmp_path}/.npm-global/bin' in path
    assert '/usr/bin' in path
    assert 'VIBE_HOME' not in env
    log = Path(plist['StandardErrorPath'])
    assert log.parent == tmp_path / 'Library' / 'Logs'
    assert plist['StandardOutPath'] == plist['StandardErrorPath']
    plistlib.loads(plistlib.dumps(plist))  # serialisable


def test_plist_carries_vibe_home_and_port(tmp_path):
    plist = build_plist(CLI, 8080, tmp_path, vibe_home='/data/vs')
    assert plist['EnvironmentVariables']['VIBE_HOME'] == '/data/vs'
    assert plist['ProgramArguments'][-1] == '8080'
    assert plist['Label'] == 'com.vibe-seller.start.8080'


def test_on_writes_plist_and_bootstraps(agent, tmp_path):
    assert agent.on(CLI, None) == 'on'
    path = tmp_path / 'Library' / 'LaunchAgents' / 'com.vibe-seller.start.plist'
    assert agent.plist_path == path
    assert plistlib.loads(path.read_bytes()) == build_plist(CLI, 7777, tmp_path)
    assert agent.fake.verbs() == ['bootstrap']
    assert agent.fake.calls[-1] == ['bootstrap', 'gui/501', str(path)]
    assert (tmp_path / 'Library' / 'Logs').is_dir()


def test_on_twice_is_a_no_op(agent):
    agent.on(CLI, None)
    assert agent.on(CLI, None) == 'already on'
    assert agent.fake.verbs() == ['bootstrap']


def test_on_reloads_a_changed_definition(agent):
    agent.on(CLI, None)
    agent.on(CLI, '/other/home')
    assert agent.fake.verbs() == ['bootstrap', 'bootout', 'bootstrap']
    env = plistlib.loads(agent.plist_path.read_bytes())['EnvironmentVariables']
    assert env['VIBE_HOME'] == '/other/home'


def test_on_loads_an_installed_but_unloaded_plist(agent):
    agent.on(CLI, None)
    agent.fake.loaded = False  # e.g. booted out by hand
    assert agent.on(CLI, None) == 'on'
    assert agent.fake.verbs() == ['bootstrap', 'bootstrap']


def test_bootstrap_retries_the_teardown_race(tmp_path, monkeypatch):
    monkeypatch.setattr(login_autostart.time, 'sleep', lambda _s: None)
    fake = FakeLaunchctl(bootstrap_failures=2)
    a = MacAgent(7777, home=tmp_path, run=fake, uid=501)
    assert a.on(CLI, None) == 'on'
    assert fake.verbs() == ['bootstrap'] * 3


def test_bootstrap_that_keeps_failing_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(login_autostart.time, 'sleep', lambda _s: None)
    fake = FakeLaunchctl(bootstrap_failures=99)
    a = MacAgent(7777, home=tmp_path, run=fake, uid=501)
    with pytest.raises(RuntimeError, match='Input/output error'):
        a.on(CLI, None)


def test_off_unloads_and_removes(agent):
    agent.on(CLI, None)
    assert agent.off() == 'off'
    assert not agent.plist_path.exists()
    assert agent.fake.loaded is False
    assert agent.fake.verbs() == ['bootstrap', 'bootout']
    assert agent.off() == 'already off'
    assert agent.fake.verbs() == ['bootstrap', 'bootout']


def test_status(agent):
    assert agent.status() == {
        'installed': False,
        'loaded': False,
        'plist': str(agent.plist_path),
    }
    agent.on(CLI, None)
    info = agent.status()
    assert info['installed'] and info['loaded']
    assert info['command'] == f'{CLI} start --port 7777'


# -- Windows ----------------------------------------------------------


def test_windows_install_dir_needs_the_tray(tmp_path, monkeypatch):
    monkeypatch.delenv('VIBE_SELLER_HOME', raising=False)
    venv = tmp_path / '.venv'
    monkeypatch.setattr(sys, 'prefix', str(venv))
    assert login_autostart.windows_install_dir() is None
    (tmp_path / 'tray.py').write_text('')
    assert login_autostart.windows_install_dir() == tmp_path


def test_windows_install_dir_env_override(tmp_path, monkeypatch):
    (tmp_path / 'tray.py').write_text('')
    monkeypatch.setenv('VIBE_SELLER_HOME', str(tmp_path))
    monkeypatch.setattr(sys, 'prefix', '/nowhere/.venv')
    assert login_autostart.windows_install_dir() == tmp_path


class FakeSwitch:
    instances: list['FakeSwitch'] = []

    def __init__(self, install_dir):
        self.install_dir = install_dir
        self.enabled = True
        self.lnk_path = Path(install_dir) / 'Vibe Seller.lnk'
        self.requests: list[bool] = []
        FakeSwitch.instances.append(self)

    def is_enabled(self):
        return self.enabled

    def set_enabled(self, on):
        self.requests.append(on)
        self.enabled = on
        return on


@pytest.fixture
def windows(tmp_path, monkeypatch):
    (tmp_path / 'tray.py').write_text('')
    monkeypatch.setenv('VIBE_SELLER_HOME', str(tmp_path))
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setattr(windows_autostart, 'AutoStart', FakeSwitch)
    FakeSwitch.instances.clear()
    return tmp_path


@pytest.mark.parametrize(
    ('action', 'requests'),
    [('on', [True]), ('off', [False]), ('status', [])],
)
def test_windows_drives_the_tray_switch(windows, capsys, action, requests):
    assert cli.main(['autostart', action]) == 0
    (switch,) = FakeSwitch.instances
    assert switch.install_dir == windows
    assert switch.requests == requests
    expected = 'off' if action == 'off' else 'on'
    assert f'Start at login: {expected}' in capsys.readouterr().out


def test_windows_without_the_installer_is_refused(tmp_path, monkeypatch):
    monkeypatch.delenv('VIBE_SELLER_HOME', raising=False)
    monkeypatch.setattr(sys, 'prefix', str(tmp_path / '.venv'))
    monkeypatch.setattr(sys, 'platform', 'win32')
    assert cli.main(['autostart', 'on']) == 2


def test_tray_and_cli_share_one_implementation():
    win_dir = Path(__file__).resolve().parents[2] / 'installer' / 'windows'
    if str(win_dir) not in sys.path:
        sys.path.insert(0, str(win_dir))
    import autostart  # noqa: PLC0415

    assert autostart.AutoStart is windows_autostart.AutoStart
    assert autostart.LNK_NAME == windows_autostart.LNK_NAME


# -- other platforms --------------------------------------------------


@pytest.mark.parametrize('platform', ['linux', 'cygwin'])
def test_other_platforms_are_refused(monkeypatch, capsys, platform):
    monkeypatch.setattr(sys, 'platform', platform)
    assert cli.main(['autostart', 'on']) == 2
    assert login_autostart.DOCS_URL in capsys.readouterr().err


def test_mac_cli_off_when_nothing_installed(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setattr(login_autostart.Path, 'home', lambda: tmp_path)
    fake = FakeLaunchctl()
    monkeypatch.setattr(login_autostart.subprocess, 'run', fake)
    assert cli.main(['autostart', 'status']) == 0
    assert 'Start at login: off' in capsys.readouterr().out
    assert cli.main(['autostart', 'off']) == 0
    assert 'already off' in capsys.readouterr().out
