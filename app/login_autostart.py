"""``vibe-seller autostart on|off|status`` — start the server at login.

- **macOS**: a per-user LaunchAgent that runs ``vibe-seller start`` once
  at every login (``RunAtLoad``). ``AbandonProcessGroup`` lets the
  daemonised server outlive the short-lived launcher job, so the
  server's own PID file still drives ``vibe-seller stop`` / ``upgrade``.
  No sudo: the plist lives in ``~/Library/LaunchAgents``.
- **Windows (installer)**: the tray's Startup-folder shortcut and its
  StartupApproved flag (app/windows_autostart.py) — the same switch as
  the tray's "Start at login" item and Task Manager's Startup page.
- **Anything else** (Linux, WSL, a pip install on Windows without the
  tray): refused with a pointer to the docs; nothing is guessed.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import time

DOCS_URL = 'https://docs.vibe-sellers.com/always-on/'
DEFAULT_PORT = 7777
LABEL_PREFIX = 'com.vibe-seller.start'


def add_subparser(subs) -> None:
    """Register ``autostart`` on the top-level CLI's subparsers."""
    p = subs.add_parser(
        'autostart',
        help=(
            'Start the server automatically after you log in '
            '(macOS LaunchAgent / Windows tray). on | off | status.'
        ),
    )
    p.add_argument('action', choices=('on', 'off', 'status'))
    p.add_argument(
        '--port',
        type=int,
        default=DEFAULT_PORT,
        help='Port the server starts on (macOS; default: 7777).',
    )


# -- macOS ------------------------------------------------------------


def mac_label(port: int) -> str:
    """One LaunchAgent per port; the default port keeps the bare label."""
    if port == DEFAULT_PORT:
        return LABEL_PREFIX
    return f'{LABEL_PREFIX}.{port}'


def resolve_cli() -> str | None:
    """The ``vibe-seller`` executable launchd should run.

    The one running right now wins (an absolute ``argv[0]`` named
    vibe-seller), else the first on PATH. Symlinks are kept, not
    resolved: ``uv tool upgrade`` swaps the target, the link stays.
    """
    argv0 = Path(sys.argv[0])
    if argv0.name == 'vibe-seller' and argv0.is_file():
        return os.path.abspath(argv0)
    return shutil.which('vibe-seller')


def build_plist(
    cli: str,
    port: int,
    home: Path,
    vibe_home: str | None = None,
) -> dict:
    """The LaunchAgent definition (a plain dict, for plistlib)."""
    path_dirs = [
        str(Path(cli).parent),
        f'{home}/.local/bin',
        f'{home}/.npm-global/bin',
        '/opt/homebrew/bin',
        '/usr/local/bin',
        '/usr/bin',
        '/bin',
        '/usr/sbin',
        '/sbin',
    ]
    env = {
        'HOME': str(home),
        'PATH': ':'.join(dict.fromkeys(path_dirs)),
    }
    if vibe_home:
        env['VIBE_HOME'] = vibe_home
    log = str(home / 'Library' / 'Logs' / f'{mac_label(port)}.log')
    return {
        'Label': mac_label(port),
        'ProgramArguments': [cli, 'start', '--port', str(port)],
        'EnvironmentVariables': env,
        'RunAtLoad': True,
        'AbandonProcessGroup': True,
        'StandardOutPath': log,
        'StandardErrorPath': log,
    }


class MacAgent:
    """Install / remove / inspect the LaunchAgent for one port.

    ``agents_dir`` and ``run`` are injectable so tests never touch the
    real ``~/Library/LaunchAgents`` or call launchctl.
    """

    def __init__(
        self,
        port: int,
        home: Path | None = None,
        agents_dir: Path | None = None,
        run=None,
        uid: int | None = None,
    ) -> None:
        self.port = port
        self.home = home or Path.home()
        self.agents_dir = agents_dir or (self.home / 'Library' / 'LaunchAgents')
        self._run = run or subprocess.run
        self.uid = os.getuid() if uid is None else uid
        self.label = mac_label(port)

    @property
    def plist_path(self) -> Path:
        return self.agents_dir / f'{self.label}.plist'

    @property
    def target(self) -> str:
        return f'gui/{self.uid}/{self.label}'

    def _launchctl(self, *args: str) -> subprocess.CompletedProcess:
        return self._run(
            ['launchctl', *args], capture_output=True, text=True, check=False
        )

    def is_loaded(self) -> bool:
        return self._launchctl('print', self.target).returncode == 0

    def is_installed(self) -> bool:
        return self.plist_path.is_file()

    def _bootstrap(self) -> subprocess.CompletedProcess:
        # Right after a bootout launchd may still be tearing the label
        # down ("5: Input/output error") — retry briefly.
        for attempt in range(5):
            done = self._launchctl(
                'bootstrap', f'gui/{self.uid}', str(self.plist_path)
            )
            if done.returncode == 0:
                break
            if attempt < 4:
                time.sleep(1)
        return done

    def on(self, cli: str, vibe_home: str | None) -> str:
        data = plistlib.dumps(build_plist(cli, self.port, self.home, vibe_home))
        unchanged = self.is_installed() and self.plist_path.read_bytes() == data
        if unchanged and self.is_loaded():
            return 'already on'
        (self.home / 'Library' / 'Logs').mkdir(parents=True, exist_ok=True)
        self.agents_dir.mkdir(parents=True, exist_ok=True)
        if not unchanged:
            tmp = self.plist_path.with_suffix('.plist.tmp')
            tmp.write_bytes(data)
            tmp.replace(self.plist_path)
        if self.is_loaded():
            # A changed definition only takes effect after a reload. The
            # server itself is not this job (AbandonProcessGroup), so a
            # bootout does not stop it.
            self._launchctl('bootout', self.target)
        done = self._bootstrap()
        if done.returncode != 0:
            raise RuntimeError(
                'launchctl bootstrap failed: '
                + (done.stderr or done.stdout or '').strip()
            )
        return 'on'

    def off(self) -> str:
        if self.is_loaded():
            self._launchctl('bootout', self.target)
        if self.is_installed():
            self.plist_path.unlink()
            return 'off'
        return 'already off'

    def status(self) -> dict:
        info = {
            'installed': self.is_installed(),
            'loaded': self.is_loaded(),
            'plist': str(self.plist_path),
        }
        if info['installed']:
            with open(self.plist_path, 'rb') as fp:
                info['command'] = ' '.join(
                    plistlib.load(fp).get('ProgramArguments', [])
                )
        return info


# -- Windows ----------------------------------------------------------


def windows_install_dir() -> Path | None:
    """The installer's app dir, when this CLI runs from it.

    The installer puts the venv at ``{app}\\.venv`` and tray.py at
    ``{app}``; VIBE_SELLER_HOME overrides it exactly as it does for the
    tray. None when there is no tray to start (e.g. a pip install).
    """
    env = os.environ.get('VIBE_SELLER_HOME')
    base = Path(env) if env else Path(sys.prefix).parent
    return base if (base / 'tray.py').is_file() else None


def _windows(action: str) -> int:
    from app.windows_autostart import (  # noqa: PLC0415 — Windows only
        AutoStart,
        AutoStartError,
    )

    install_dir = windows_install_dir()
    if install_dir is None:
        print(
            'Start at login on Windows comes with the Vibe Seller '
            'installer (VibeSeller-Setup.exe), and this install has no '
            f'tray to start. See {DOCS_URL}',
            file=sys.stderr,
        )
        return 2
    switch = AutoStart(install_dir=install_dir)
    try:
        if action == 'status':
            state = switch.is_enabled()
        else:
            state = switch.set_enabled(action == 'on')
    except (AutoStartError, OSError) as exc:
        print(f'autostart {action} failed: {exc}', file=sys.stderr)
        return 1
    print(f'Start at login: {"on" if state else "off"} ({switch.lnk_path})')
    return 0


def _mac(args: argparse.Namespace) -> int:
    agent = MacAgent(args.port)
    if args.action == 'status':
        info = agent.status()
        state = 'on' if info['installed'] else 'off'
        print(f'Start at login: {state}')
        print(f'  plist:  {info["plist"]}')
        print(f'  loaded: {"yes" if info["loaded"] else "no"}')
        if 'command' in info:
            print(f'  runs:   {info["command"]}')
        return 0
    if args.action == 'off':
        print(f'Start at login: {agent.off()}')
        return 0
    cli = resolve_cli()
    if cli is None:
        print('Cannot find the vibe-seller command on PATH.', file=sys.stderr)
        return 1
    try:
        result = agent.on(cli, os.environ.get('VIBE_HOME'))
    except RuntimeError as exc:
        print(f'autostart on failed: {exc}', file=sys.stderr)
        return 1
    print(f'Start at login: {result}')
    print(f'  plist: {agent.plist_path}')
    print(f'  check: vibe-seller autostart status --port {args.port}')
    return 0


def cmd_autostart(args: argparse.Namespace) -> int:
    if sys.platform == 'darwin':
        return _mac(args)
    if sys.platform == 'win32':
        return _windows(args.action)
    print(
        '`vibe-seller autostart` supports macOS and the Windows installer '
        f'only. For Linux / WSL see {DOCS_URL}',
        file=sys.stderr,
    )
    return 2
