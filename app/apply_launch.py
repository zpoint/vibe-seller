"""Launch the detached apply: the argument document, and how to hand it over.

Split out of ``app/pip_update.py`` (which decides WHETHER the pip path
applies and PROVES it can run offline) because this half answers a
different question: what a process that is about to stop the running
server needs to know about the machine. Everything here is decided once,
in one document, and handed to ``app/pip_apply_helper.py`` — which may
not import the package it is replacing, so the document has to be
complete: the exact argv the update check proved, the port THIS server
actually listens on, who is allowed to restart it, and the environment
to come back under.

Two invariants the split is built around.

**The promise and the act are one command.** ``install_command`` reuses
``pip_update.apply_command``, the same builder the offline dry-run gate
ran, differing by one flag. A helper that rebuilt the argv could drift
from the gate that green-lit it, and the drift would only show up at
03:00 on a machine nobody is watching.

**Dark is the only licence to start.** Under launchd or systemd the
supervisor owns the server process; a second copy started beside it
races for the port and the database, outside the unit's logs and limits
(review 2 MUST 2). ``supervisor`` therefore errs toward "unsupervised"
for any ``application.*`` session — a GUI terminal is not a supervisor,
and mis-detecting one would leave the machine dark after a failed
update (review 3).

Everything here blocks (subprocess, the file system), so every entry
point is called through ``asyncio.to_thread`` by ``app/auto_update.py``.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys

from app import bundle, pip_update, windows_update
from app.release_channel import channel_of
from app.version import get_version

logger = logging.getLogger(__name__)

#: The helper script, copied out of the package before it runs: on
#: Windows it must not be loaded from the venv it is about to replace.
#: Its argument document travels next to it.
HELPER_MODULE = 'pip_apply_helper.py'
HELPER_ARGS_NAME = 'pip-apply.json'


def unix_upgrade_argv(version: str) -> list[str]:
    """How to run the REAL ``vibe-seller upgrade`` from this install —
    one source of truth for what an upgrade does (package + Chromium +
    Claude Code CLI, app/cli.py) — pinned to *version*.

    The version travels because the update check already answered the
    question "which release, by which path": it fingerprinted this
    release, warmed its wheels and pre-installed the browser THIS
    release's Playwright asks for. ``upgrade`` with no version asks the
    index what is newest *now*, which is a different question and can
    answer differently — a release published in the minutes between the
    check and the apply, or (the case the e2e caught on 2026-11-13) any
    newer wheel sitting in a find-links directory. The apply would then
    install a build nobody checked and record an update to the version
    it was told about. ``app/cli.py`` keeps the tool receipt unpinned
    afterwards, so naming the version costs nothing the next upgrade
    needs.
    """
    script = Path(sys.executable).parent / 'vibe-seller'
    if script.exists():
        return [str(script), 'upgrade', '--version', version]
    return [
        sys.executable,
        '-I',
        '-m',
        'app.cli',
        'upgrade',
        '--version',
        version,
    ]


def _cli_argv(*args: str) -> list[str]:
    script = Path(sys.executable).parent / 'vibe-seller'
    if script.exists():
        return [str(script), *args]
    return [sys.executable, '-I', '-m', 'app.cli', *args]


def install_command(version: str, path: str) -> list[str]:
    """The one argv that installs the update, per platform and path.

    Windows + pip is the offline uv command the gate just proved; the
    other combinations are the installer (#244's path, which never comes
    through a helper) or the real upgrade command.
    """
    if pip_update.is_windows_bundle():
        return pip_update.apply_command(version)
    return unix_upgrade_argv(version)


def apply_args(version: str, trigger: str, path: str) -> dict:
    """The single argument document the helper reads.

    The install command travels inside it as data, so what the update
    check proved is byte-for-byte what runs — a helper that rebuilt the
    argv could drift from the gate that green-lit it. Everything the
    helper needs to bring the app back up is here too, because by then
    this process may not exist.
    """
    port = _server_port()
    app = bundle.app_root()
    where = pip_update.updates_dir()
    env = {
        # Not derived from `updates_dir`'s parent: the helper must land
        # on the SAME data root this server was started with, whatever
        # the shape of the tree under it.
        'VIBE_HOME': str(pip_update.vibe_home()),
        'VIBE_SELLER_PORT': str(port),
    }
    if app is not None:
        env['VIBE_SELLER_HOME'] = str(app)
    for name in ('LOCALAPPDATA', 'APPDATA', 'USERPROFILE', 'TEMP', 'TMP'):
        if name in os.environ:
            env[name] = os.environ[name]
    scripts = Path(sys.executable).parent
    args = {
        'platform': 'win32' if pip_update.is_windows_bundle() else sys.platform,
        'path': path,
        'app_dir': str(app) if app is not None else str(scripts.parent),
        'uv': pip_update.uv_binary(),
        'venv_python': pip_update.venv_python(),
        'updates_dir': str(where),
        'record': str(pip_update.record_path()),
        'install_command': install_command(version, path),
        'stop_command': _cli_argv('stop', '--port', str(port)),
        'start_command': _cli_argv('start', '--port', str(port)),
        'tray_python': str(scripts / 'pythonw.exe'),
        'port': port,
        'from_version': get_version(),
        'target_version': version,
        'trigger': trigger,
        'channel': channel_of(get_version()),
        # Who is allowed to bring the server back, and where only one
        # apply at a time may lock.
        'supervisor': supervisor(),
        'lock': str(where / pip_update.APPLY_LOCK_NAME),
        'env': env,
    }
    return args


def apply(version: str, trigger: str, path: str) -> subprocess.Popen:
    """Start the detached apply helper and hand it the machine.

    The helper outlives this process by design: it has to stop the
    server running the code that spawned it, replace that install, and
    bring the app back — or put the old version back and bring THAT up
    (``app/pip_apply_helper.py``). So the script is copied out of the
    package first, and run with an interpreter that is not the install
    being replaced, detached, with breakaway from any job object the
    parent is in.
    """
    where = pip_update.updates_dir()
    where.mkdir(parents=True, exist_ok=True)
    held = pip_update.apply_in_progress()
    if held is not None:
        raise pip_update.ApplyInProgress(held)
    args_file = where / HELPER_ARGS_NAME
    args_file.write_text(
        json.dumps(
            apply_args(version, trigger, path), indent=2, sort_keys=True
        ),
        encoding='utf-8',
    )
    helper = where / HELPER_MODULE
    shutil.copyfile(Path(__file__).resolve().parent / HELPER_MODULE, helper)
    log = open(  # noqa: SIM115 — the child inherits it; our copy closes below
        where / 'pip-apply.log', 'ab'
    )
    try:
        return _popen_detached(
            [pip_update.prefetch_python(), str(helper), str(args_file)], log
        )
    finally:
        log.close()


def _popen_detached(argv: list[str], log) -> subprocess.Popen:
    """Same breakaway-then-plain fallback ``windows_update.run_installer``
    needs: a job object that kills its whole tree on close (GitHub
    Actions, some supervisors) would take the helper down with the
    server it is replacing."""
    if not pip_update.is_windows_bundle():
        return subprocess.Popen(  # noqa: S603
            argv,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    base = windows_update.DETACHED_PROCESS | (
        windows_update.CREATE_NEW_PROCESS_GROUP
    )
    for creationflags in (
        base | windows_update.CREATE_BREAKAWAY_FROM_JOB,
        base,
    ):
        try:
            return subprocess.Popen(  # noqa: S603
                argv,
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
                close_fds=True,
            )
        except OSError:
            continue
    raise RuntimeError('could not start the update helper')


def supervisor() -> str | None:
    """``'systemd'``, ``'launchd'`` or None: does a supervisor restart this
    server for us?

    It matters because starting a second copy is a bug, not a fallback:
    two servers race for the port and the database, and one of them is
    outside the supervisor meant to own it — which is exactly what the
    launchd/systemd setups in docs/macos-setup.md exist to prevent.
    (Review 2 MUST 2.)

    systemd marks every process it starts with ``INVOCATION_ID``, and a
    ``NOTIFY_SOCKET`` when ``Type=notify``.

    launchd is the trap. ``XPC_SERVICE_NAME`` carries a LaunchAgent's OWN
    LABEL (``com.vibe-seller.server``, the one docs/macos-setup.md
    installs) — but it is ALSO set for anything spawned out of a GUI app,
    whose value is the app's session: ``application.com.googlecode.iTerm2
    .<id>`` for iTerm, Terminal.app, VS Code's integrated terminal. That
    value is inherited by every shell, by tmux, and so by a plain
    ``vibe-seller start`` run in one. Read it as "supervised" and the
    helper stops the server expecting a relaunch that will never come:
    a dark machine, the outcome this whole module exists to prevent. So
    an ``application.`` session is explicitly UNSUPERVISED, and only a
    label-shaped value counts.
    """
    if os.environ.get('INVOCATION_ID') or os.environ.get('NOTIFY_SOCKET'):
        return 'systemd'
    name = (os.environ.get('XPC_SERVICE_NAME') or '').strip()
    if not name or name == '0' or name.startswith('application.'):
        return None
    return 'launchd'


def listening_ports() -> list[int]:
    """The ports THIS process is listening on, de-duplicated.

    Empty when the answer cannot be had (no psutil, a container that
    hides it, a reload worker). The caller treats that as "no opinion".
    """
    try:
        import psutil  # noqa: PLC0415 — an optional view of the OS
    except ImportError:
        return []
    try:
        proc = psutil.Process()
        try:
            conns = proc.net_connections(kind='inet')
        except AttributeError:  # psutil < 6
            conns = proc.connections(kind='inet')  # noqa: PLW2901
    except Exception:  # noqa: BLE001 — no answer is better than a wrong one
        return []
    ports = {
        int(c.laddr[1])
        for c in conns
        if str(c.status).upper().endswith('LISTEN') and c.laddr
    }
    return sorted(ports)


def _server_port() -> int:
    """The port the update helper must stop, start and health-probe.

    Not ``app.config.BACKEND_PORT`` — that is the *default*, and a
    server started as ``vibe-seller start --port 7792`` (every test
    instance, ``start.sh`` on a busy port, a demo on the side) is not
    on it. This number is handed to a detached process that will run
    ``stop``, ``start`` and poll ``/api/version``, so a default here
    makes it act on somebody else's server: measured 2026-11-13 in the
    Linux end-to-end, the argument document said 7777 while the leg ran
    on 7791, so the helper watched a port nothing served, concluded the
    update had failed, rolled back — and started the old version on
    7777, beside the instance that machine really runs.

    Three sources, in order of how directly they answer the question:
    the socket this process has actually bound; the command line
    ``app/upgrade_restart.py`` would re-exec from (same truth, second
    hand); and the configured default, which is right exactly when
    nothing said otherwise.
    """
    ports = listening_ports()
    if len(ports) == 1:
        return ports[0]
    argv = list(getattr(sys, 'orig_argv', []) or [])
    for i, arg in enumerate(argv):
        if arg in ('--port', '-p') and i + 1 < len(argv):
            try:
                return int(argv[i + 1])
            except ValueError:
                break
        if arg.startswith('--port='):
            try:
                return int(arg.split('=', 1)[1])
            except ValueError:
                break
    if len(ports) > 1:
        # A machine serving several ports from one process: the command
        # line had no answer, so the configured default is the only
        # remaining opinion, and it is wrong in a way a human can see.
        logger.warning(
            'this process listens on %s; using the configured port for '
            'the update helper instead of guessing',
            ports,
        )
    from app.config import BACKEND_PORT  # noqa: PLC0415

    return int(BACKEND_PORT)
