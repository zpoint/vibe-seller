"""CLI entry point for vibe-seller.

Registered as ``vibe-seller`` via pyproject.toml [project.scripts].
The wheel ships everything needed (skills, knowledge, built frontend),
so `pip install vibe-seller` followed by `vibe-seller start` is enough
for end users — no clone, no start.sh.

`vibe-seller start` daemonizes by default (PID + log under
~/.vibe-seller/), so closing the terminal doesn't kill the server.
Use `--foreground` to keep uvicorn in the current process (dev only).
For contributors working from a clone, start.sh adds frontend build,
PID-file daemonization, and stop/restart helpers; the CLI's
daemonization is the equivalent for the wheel-installed path.
"""

import argparse
from contextlib import suppress
from importlib.metadata import PackageNotFoundError, version as _pkg_version
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time


def _resolve_version() -> str:
    """Read the installed package version from wheel metadata.

    setuptools-scm bakes this in at build time (derived from the
    `v*` git tag). The PackageNotFoundError branch covers running
    cli.py straight out of a clone without `pip install -e .`.
    """
    try:
        return _pkg_version('vibe-seller')
    except PackageNotFoundError:
        return '0.0.0+dev'


# Resolve VIBE_HOME the same way app.config does, but without
# pulling in the rest of the app module on startup (the CLI must
# load fast and not import anything that opens a DB connection).
def _vibe_home() -> Path:
    return Path(
        os.environ.get('VIBE_HOME') or str(Path.home() / '.vibe-seller')
    )


def _pid_dir() -> Path:
    p = _vibe_home() / 'pids'
    p.mkdir(parents=True, exist_ok=True)
    return p


def _log_dir() -> Path:
    p = _vibe_home() / 'logs'
    p.mkdir(parents=True, exist_ok=True)
    return p


def _pid_file_for(port: int) -> Path:
    return _pid_dir() / f'backend_{port}.pid'


def _log_file_for(port: int) -> Path:
    return _log_dir() / f'backend_{port}.log'


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # PID exists but we can't signal it — still alive from our pov.
        return True
    return True


def _read_pid(port: int) -> int | None:
    f = _pid_file_for(port)
    if not f.exists():
        return None
    try:
        pid = int(f.read_text().strip())
    except (ValueError, OSError):
        return None
    if not _pid_alive(pid):
        with suppress(OSError):
            f.unlink()
        return None
    return pid


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='vibe-seller',
        description=(
            'Vibe Seller command-line entry. Run a subcommand to do '
            'something — bare `vibe-seller` prints this help.'
        ),
    )
    parser.add_argument(
        '--version',
        '-V',
        action='version',
        version=f'vibe-seller {_resolve_version()}',
    )
    subs = parser.add_subparsers(dest='command', metavar='<command>')
    subs.required = True

    # start ----------------------------------------------------------
    start = subs.add_parser(
        'start',
        help=(
            'Start the server in the background. Closes-terminal-safe; '
            'use `vibe-seller stop` to terminate.'
        ),
    )
    start.add_argument(
        '--port',
        type=int,
        default=7777,
        help='Port to listen on (default: 7777).',
    )
    start.add_argument(
        '--host',
        default='0.0.0.0',
        help='Host interface to bind (default: 0.0.0.0).',
    )
    start.add_argument(
        '-f',
        '--foreground',
        action='store_true',
        help=(
            'Run in the foreground (Ctrl+C to stop). Implied by '
            '--dev. Use when you want to watch logs directly.'
        ),
    )
    start.add_argument(
        '--dev',
        action='store_true',
        help=(
            'Dev mode: foreground + uvicorn auto-reload on code '
            'changes. Only meaningful from a clone where source files '
            'exist.'
        ),
    )

    # stop -----------------------------------------------------------
    stop = subs.add_parser(
        'stop',
        help='Stop a backgrounded server started with `vibe-seller start`.',
    )
    stop.add_argument(
        '--port',
        type=int,
        default=7777,
        help='Port the server is running on (default: 7777).',
    )

    # upgrade --------------------------------------------------------
    subs.add_parser(
        'upgrade',
        help=(
            'Upgrade vibe-seller in place. Uses `uv tool upgrade` when '
            'uv is on PATH (the install.sh path), falls back to '
            '`pip install --upgrade` otherwise.'
        ),
    )

    return parser


def _cmd_start(args: argparse.Namespace) -> int:
    host: str = args.host
    port: int = args.port

    # Foreground path — for dev / log-watching. uvicorn auto-reload
    # also requires the main thread, so --dev implies --foreground.
    # `import uvicorn` is lazy on purpose: `vibe-seller stop` and
    # `vibe-seller upgrade` shouldn't drag uvicorn (and FastAPI, and
    # the rest of the app graph) onto the import path.
    if args.foreground or args.dev:
        import uvicorn  # noqa: PLC0415

        uvicorn.run('app.main:app', host=host, port=port, reload=args.dev)
        return 0

    # Background daemon path -----------------------------------------
    existing = _read_pid(port)
    if existing is not None:
        print(
            f'vibe-seller already running on port {port} (PID {existing}).',
            file=sys.stderr,
        )
        print(
            f'Run `vibe-seller stop --port {port}` to stop it first.',
            file=sys.stderr,
        )
        return 1

    log_path = _log_file_for(port)
    pid_path = _pid_file_for(port)
    # Run uvicorn from VIBE_HOME so a stray `app/` directory in the
    # caller's CWD (e.g. someone running `vibe-seller start` from
    # inside a vibe-seller checkout) can't shadow the installed
    # wheel's `app` package. Python prepends CWD to sys.path on
    # import; without this Popen `cwd=` argument the wheel-installed
    # daemon ends up importing the source tree instead of itself.
    daemon_cwd = _vibe_home()
    # Open the log under `with` so ruff is happy; the OS dups the fd
    # into the Popen child, so closing the parent handle right after
    # spawn is fine — the child keeps writing through its own fd.
    with open(log_path, 'ab') as log_fp:
        proc = subprocess.Popen(
            [
                sys.executable,
                '-m',
                'uvicorn',
                'app.main:app',
                '--host',
                host,
                '--port',
                str(port),
            ],
            cwd=str(daemon_cwd),
            stdout=log_fp,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

    # Give uvicorn ~2s to boot or fail fast so we can report
    # something useful instead of "started" + immediate crash.
    time.sleep(2)
    if proc.poll() is not None:
        with suppress(OSError):
            pid_path.unlink(missing_ok=True)
        print(
            f'vibe-seller failed to start (exit {proc.returncode}). '
            f'Last lines of {log_path}:',
            file=sys.stderr,
        )
        try:
            tail = log_path.read_text(errors='replace').splitlines()[-15:]
            for line in tail:
                print(f'  {line}', file=sys.stderr)
        except OSError:
            pass
        return proc.returncode or 1

    pid_path.write_text(str(proc.pid))
    print(f'vibe-seller running on http://{host}:{port}  (PID {proc.pid})')
    print(f'  logs: {log_path}')
    print(f'  stop: vibe-seller stop --port {port}')
    return 0


def _cmd_stop(args: argparse.Namespace) -> int:
    port: int = args.port
    pid = _read_pid(port)
    if pid is None:
        print(f'No vibe-seller process tracked on port {port}.')
        return 0

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        print(f'PID {pid} not found (already exited).')
        _pid_file_for(port).unlink(missing_ok=True)
        return 0

    # Wait briefly for graceful shutdown.
    for _ in range(20):
        if not _pid_alive(pid):
            break
        time.sleep(0.25)
    else:
        # Still alive — escalate.
        with suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)

    _pid_file_for(port).unlink(missing_ok=True)
    print(f'Stopped vibe-seller on port {port} (PID {pid}).')
    return 0


def _cmd_upgrade() -> int:
    # `uv tool upgrade vibe-seller` is the canonical path since
    # install.sh uses `uv tool install`. Falling back to pip keeps the
    # subcommand useful for the manual `pip install vibe-seller` user.
    if shutil.which('uv'):
        cmd = ['uv', 'tool', 'upgrade', 'vibe-seller']
    else:
        cmd = [
            sys.executable,
            '-m',
            'pip',
            'install',
            '--upgrade',
            'vibe-seller',
        ]
    print(f'==> {" ".join(cmd)}')
    return subprocess.run(cmd).returncode


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == 'start':
        return _cmd_start(args)
    if args.command == 'stop':
        return _cmd_stop(args)
    if args.command == 'upgrade':
        return _cmd_upgrade()
    return 1  # unreachable: subparsers.required = True


if __name__ == '__main__':
    sys.exit(main())
