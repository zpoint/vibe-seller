"""The pip path: install a release with uv, over the venv already there.

Most releases change nothing but the Python package. When that is true
— and ``app/bundle.py``'s fingerprint is what makes it knowable — the
update is a few MB of wheels into the venv the app is running in,
instead of the ~700 MB Windows installer. macOS/Linux always take this
path (there is no installer there); Windows takes it only when its
installed bundle matches the release's.

Two rules shape the module:

**Prove it before promising it.** The update reaches ``ready`` only
after the target's wheels are in the uv cache AND an offline dry run of
the exact apply command succeeds against the real venv. ``ready`` then
means "applying this needs no network", which is what makes an
unattended restart at 03:00 safe. An install that cannot be completed
offline is not an error — it means the release changed something the
cache does not hold, and it goes to the installer instead.

**Warm the cache from outside the install.** The prefetch resolves the
target with ``uv tool run`` using an interpreter that is NOT the venv
being updated (see :func:`prefetch_python`) — handing ``uv tool run``
the tool venv's own python installs into it, which was observed moving
a running server onto a new version with nobody asking. The version on
disk is asserted unchanged afterwards, so the prefetch is structurally
unable to apply an update early.

Everything here blocks (subprocess, uv, the file system), so every entry
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

from app import bundle, upgrade_restart, windows_update
from app.release_channel import channel_of
from app.version import get_version

logger = logging.getLogger(__name__)

#: The two update paths, as recorded and reported.
PATH_PIP = 'pip'
PATH_INSTALLER = 'installer'

#: Triggers, so the record says who asked.
TRIGGER_AUTO_IDLE = 'auto_idle'
TRIGGER_MANUAL = 'manual'
TRIGGER_MANUAL_AFTER_24H = 'manual_after_24h'

PREFETCH_TIMEOUT_SECONDS = 900.0
DRY_RUN_TIMEOUT_SECONDS = 300.0

#: How long the Unix apply watcher waits for the detached
#: ``vibe-seller upgrade`` to change the installed version.
APPLY_TIMEOUT_SECONDS = 15 * 60

#: The helper script, copied out of the package before it runs: on
#: Windows it must not be loaded from the venv it is about to replace.
HELPER_MODULE = 'pip_apply_helper.py'
HELPER_ARGS_NAME = 'pip-apply.json'
RECORD_NAME = 'last.json'
#: One apply at a time, machine-wide (review 2 SHOULD 3).
APPLY_LOCK_NAME = 'apply.lock'


#: Outcome values in ``last.json``, as written by
#: ``app/pip_apply_helper.py``. Repeated there as literals because that
#: script must not import anything from the package it replaces, and the
#: repetition is pinned by
#: ``tests/unit/test_pip_apply_helper.py``.
ROLLED_BACK = 'rolled_back'
STATUS_OK = 'ok'
STATUS_FAILED = 'failed'


class ApplyInProgress(RuntimeError):
    """Another apply holds the single-flight lock.

    A distinct type because it is not a failure worth reporting as a
    500: the router turns it into a 409, and the UI says "wait for the
    one already running" rather than "the updater is broken"."""


class PrefetchError(RuntimeError):
    """The target could not be fetched into the cache (yet)."""


# ── where things live ────────────────────────────────────────────────


def vibe_home() -> Path:
    """The app's data root, as this process was told to see it.

    Late import: ``app.config`` creates the data directory at import
    time, a side effect that belongs to server boot, not to every
    importer.
    """
    from app.config import VIBE_SELLER_DIR  # noqa: PLC0415

    return VIBE_SELLER_DIR


def updates_dir() -> Path:
    """``<VIBE_HOME>/updates``."""
    return vibe_home() / 'updates'


def record_path() -> Path:
    return updates_dir() / RECORD_NAME


def is_windows_bundle() -> bool:
    """The installer-bundle layout the pip path exists for.

    THE question for every branch in this module, asked once: ``app/bundle.py``
    answers it from the install layout (a Windows install made by the
    installer has a bundle dir above the venv; nothing else does). Scattered
    ``sys.platform`` checks instead would let one branch disagree with
    another — a venv on Linux that happens to be laid out like a bundle
    would get Windows commands, and vice versa.
    """
    return bundle.app_root() is not None


def venv_python() -> str:
    """The interpreter of the venv being updated."""
    return sys.executable


def uv_binary() -> str:
    """The uv that will do the install.

    Windows uses the bundle's own ``{app}\\uv.exe`` — the one that built
    this venv, whose version the bundle fingerprint pins. Any uv found
    on PATH could be a different one that resolves differently.
    """
    if is_windows_bundle():
        app = bundle.app_root()
        assert app is not None  # is_windows_bundle just said so
        return str(app / 'uv.exe')
    found = shutil.which('uv')
    if not found:
        raise PrefetchError('uv is not on PATH')
    return found


def prefetch_python() -> str:
    """An interpreter to resolve the target with, outside the install.

    On Windows that is the bundle's own base Python (``{app}\\python\\
    python.exe``): same version and platform as the venv it built, so
    the wheels it resolves are the ones the venv will install, and it is
    not an install target.

    Elsewhere: ``sys._base_executable`` (what venvs record; only when it
    lives outside this venv), then the usual names under
    ``<base_prefix>/bin`` — Homebrew/Debian/python.org bases often ship
    ``python3`` or ``python3.X`` and no bare ``python``. Finding none
    fails CLOSED: with no interpreter outside the venv there is no safe
    prefetch, and falling back to the dangerous one is the bug this
    function exists to prevent.
    """
    if is_windows_bundle():
        app = bundle.app_root()
        assert app is not None
        base = app / 'python' / 'python.exe'
        if not base.is_file():
            raise PrefetchError(f'the bundled base python is missing: {base}')
        return str(base)
    prefix = Path(sys.prefix).resolve()
    candidates: list[str] = []
    base_exe = getattr(sys, '_base_executable', '') or ''
    if base_exe:
        candidates.append(base_exe)
    bin_dir = Path(sys.base_prefix) / 'bin'
    ver = f'python{sys.version_info[0]}.{sys.version_info[1]}'
    candidates += [str(bin_dir / n) for n in (ver, 'python3', 'python')]
    for cand in candidates:
        try:
            resolved = Path(cand).resolve()
        except OSError:
            continue
        if not resolved.exists():
            continue
        if resolved.is_relative_to(prefix):
            # Inside the tool venv — exactly the interpreter that must
            # never be handed to `uv tool run`.
            continue
        return str(resolved)
    raise PrefetchError(
        'no base interpreter found for the prefetch (refusing to use the '
        'installed tool venv python)'
    )


# ── the commands ─────────────────────────────────────────────────────


def warm_cache_argv(version: str, *args: str) -> list[str]:
    """``uv tool run`` the target's CLI in an ephemeral env.

    Resolves and downloads the pinned version and its dependencies into
    the shared uv cache, then runs the fresh CLI — which proves the
    wheels are usable, not merely fetched. The pin mirrors
    ``app/cli.py``'s nightly rule (an exact ``==`` of a pre-release is
    allowed without enabling pre-releases globally).
    """
    return [
        uv_binary(),
        'tool',
        'run',
        '--from',
        f'vibe-seller=={version}',
        '--python',
        prefetch_python(),
        'vibe-seller',
        *args,
    ]


def bundle_wheels_dir() -> Path | None:
    """``{app}\\wheels`` — the wheel set the last installer laid down."""
    app = bundle.app_root()
    if app is None:
        return None
    wheels = app / 'wheels'
    return wheels if wheels.is_dir() else None


def bundle_covers(version: str) -> bool:
    """Whether the bundle's own wheel set still carries *version*.

    True only for the version the installer shipped with. After one pip
    update the installed version exists nowhere in ``{app}\\wheels`` —
    which is exactly why a rollback cannot be ``--no-index`` alone
    (review 2 MUST 1), and why the cache has to hold it too.
    """
    wheels = bundle_wheels_dir()
    if wheels is None:
        return False
    stem = f'vibe_seller-{version}-'
    try:
        return any(path.name.startswith(stem) for path in wheels.iterdir())
    except OSError:
        return False


def find_links() -> list[str]:
    """Every local wheel source the apply may resolve from, in order.

    The environment first, because the prefetch reads it and the apply
    must not see a narrower world than the check that approved it; then
    ``{app}\\wheels``, the set the installer laid down, which a second
    local source the apply may also fall back to.

    They cannot be passed as one flag each way round: ``--find-links``
    on the command line REPLACES ``UV_FIND_LINKS`` in the environment
    (uv 0.5.18, measured on the Windows host — the env alone resolves
    the wheel, env-plus-flag does not). Naming only the bundle's wheels
    therefore drops whatever the environment configured: a corporate or
    CN mirror, and the fixture directories the end-to-end legs use. The
    consequence is silent and total — the offline dry run refuses, the
    pip path is abandoned, and every install quietly takes the ~700 MB
    installer path for every release.
    """
    sources: list[str] = []
    env = (os.environ.get('UV_FIND_LINKS') or '').strip()
    if env:
        sources += [part.strip() for part in env.split(',') if part.strip()]
    wheels = bundle_wheels_dir()
    if wheels is not None:
        sources.append(str(wheels))
    return sources


def apply_command(version: str, *, dry_run: bool = False) -> list[str]:
    """The Windows pip apply, as one argv.

    The dry-run gate and the real apply are built HERE and nowhere else,
    differing by one flag: what was proven complete is what runs.

    ``--offline`` is the point. uv may only use what the cache already
    holds, so a success means an unattended restart will not stall on a
    network fetch, and a failure means the release changed dependency
    bytes the bundle has no copy of — which is the installer's job.
    """
    argv = [
        uv_binary(),
        'pip',
        'install',
        '--python',
        venv_python(),
    ]
    if dry_run:
        argv.append('--dry-run')
    argv += ['--offline']
    for source in find_links():
        argv += ['--find-links', source]
    argv += [
        # Replace the app's own files even when the version looks
        # unchanged, as BuildRuntimeEnv does.
        '--reinstall-package',
        'vibe-seller',
        f'vibe-seller=={version}',
    ]
    return argv


# ── prefetch ─────────────────────────────────────────────────────────


def _run(argv: list[str], timeout: float) -> subprocess.CompletedProcess:
    logger.info('pip update: %s', ' '.join(argv))
    return subprocess.run(  # noqa: S603
        argv, capture_output=True, text=True, timeout=timeout
    )


def _tail(done: subprocess.CompletedProcess) -> str:
    return ((done.stderr or '') + (done.stdout or '')).strip()[-400:]


def _assert_install_untouched() -> None:
    """A prefetch that moved the installed version applied an update
    nobody asked for. Refuse to continue rather than explain it later."""
    on_disk = upgrade_restart.installed_version()
    if on_disk != get_version():
        raise PrefetchError(
            f'the prefetch moved the installed tool to {on_disk} — '
            'refusing to continue (an update must never apply itself)'
        )


def warm(version: str) -> None:
    """Fetch the target and its dependencies into the uv cache."""
    done = _run(warm_cache_argv(version, '--version'), PREFETCH_TIMEOUT_SECONDS)
    if done.returncode != 0:
        raise PrefetchError(
            f'uv could not fetch vibe-seller=={version} '
            f'({done.returncode}): {_tail(done)}'
        )
    _assert_install_untouched()


def pre_install_browser(version: str) -> None:
    """Fetch the Chromium the TARGET's Playwright pins (macOS/Linux).

    Run from the target's ephemeral env, so it is the new version's code
    and the new Playwright choosing the revision — and it uses the same
    npmmirror/CDN fallback ``vibe-seller install-browser`` has. Doing it
    during the prefetch is what keeps the restart itself free of a
    ~150 MB download: an unattended restart that stalls on a browser
    fetch is an update that never finishes.

    Best effort. A failure here costs a slower restart, not a broken
    install — the server installs the browser at startup anyway
    (``app/browser/chromium_engine.py``), so the update still applies.
    """
    if is_windows_bundle():
        # The browser is part of the bundle: a pip update cannot change
        # which revision is installed, and the fingerprint already said
        # it did not.
        return
    done = _run(
        warm_cache_argv(version, 'install-browser'), PREFETCH_TIMEOUT_SECONDS
    )
    if done.returncode != 0:
        logger.warning(
            'could not pre-install the target browser (the server will '
            'retry at startup): %s',
            _tail(done),
        )
    _assert_install_untouched()


def keep_rollback_warm() -> None:
    """Hold the RUNNING version's wheels in the cache (macOS/Linux).

    The apply helper's recovery path is `uv tool install --force
    vibe-seller==<old>`; that is offline only while the old wheels are
    still in the cache, and nothing guarantees they survived months of
    other installs. Warming it here costs one resolution per update
    check; the alternative is a rollback that needs a network the failed
    update may not have.

    Best effort — a machine that cannot re-fetch what it already has
    installed is not a reason to skip the update."""
    current = get_version()
    if is_windows_bundle() and bundle_covers(current):
        # The installer's own wheel set still holds this version, so a
        # rollback can already be offline without the cache. From the
        # first pip update on it does NOT, and the cache is what has to
        # carry it (review 2 MUST 1).
        return
    try:
        done = _run(
            warm_cache_argv(current, '--version'), PREFETCH_TIMEOUT_SECONDS
        )
        if done.returncode != 0:
            logger.info(
                'could not warm the rollback copy of %s: %s',
                current,
                _tail(done),
            )
    except Exception as exc:  # noqa: BLE001 — insurance, not a gate
        logger.info('rollback warm-up failed for %s: %s', current, exc)
    else:
        _assert_install_untouched()


def offline_apply_proven(version: str) -> str | None:
    """None when the offline apply is complete, else why it is not.

    The dry run is the only check that says "applying this will need
    nothing from the network", because it resolves the exact command
    against the exact venv it will be installed into.
    """
    try:
        done = _run(
            apply_command(version, dry_run=True), DRY_RUN_TIMEOUT_SECONDS
        )
    except OSError as exc:
        return f'could not run the offline check: {exc}'
    if done.returncode != 0:
        return (
            f'the offline install would not be complete '
            f'({done.returncode}): {_tail(done)}'
        )
    return None


def available() -> bool:
    """Whether the pip path can run here at all, shape-of-install aside.

    Checked per cycle, because a bundle missing its uv.exe or base
    python is a bundle that can only be replaced by an installer.
    """
    if is_windows_bundle():
        app = bundle.app_root()
        if app is None:
            return False
        return (app / 'uv.exe').is_file() and (
            app / 'python' / 'python.exe'
        ).is_file()
    return shutil.which('uv') is not None


# ── apply ────────────────────────────────────────────────────────────


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
    if is_windows_bundle():
        return apply_command(version)
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
    where = updates_dir()
    env = {
        # Not derived from `updates_dir`'s parent: the helper must land
        # on the SAME data root this server was started with, whatever
        # the shape of the tree under it.
        'VIBE_HOME': str(vibe_home()),
        'VIBE_SELLER_PORT': str(port),
    }
    if app is not None:
        env['VIBE_SELLER_HOME'] = str(app)
    for name in ('LOCALAPPDATA', 'APPDATA', 'USERPROFILE', 'TEMP', 'TMP'):
        if name in os.environ:
            env[name] = os.environ[name]
    scripts = Path(sys.executable).parent
    args = {
        'platform': 'win32' if is_windows_bundle() else sys.platform,
        'path': path,
        'app_dir': str(app) if app is not None else str(scripts.parent),
        'uv': uv_binary(),
        'venv_python': venv_python(),
        'updates_dir': str(where),
        'record': str(record_path()),
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
        'lock': str(where / APPLY_LOCK_NAME),
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
    where = updates_dir()
    where.mkdir(parents=True, exist_ok=True)
    held = apply_in_progress()
    if held is not None:
        raise ApplyInProgress(held)
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
            [prefetch_python(), str(helper), str(args_file)], log
        )
    finally:
        log.close()


def _popen_detached(argv: list[str], log) -> subprocess.Popen:
    """Same breakaway-then-plain fallback ``windows_update.run_installer``
    needs: a job object that kills its whole tree on close (GitHub
    Actions, some supervisors) would take the helper down with the
    server it is replacing."""
    if not is_windows_bundle():
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


# ── the record ───────────────────────────────────────────────────────


def apply_in_progress() -> str | None:
    """A message when another apply holds the lock, else None.

    The lock is advisory but OS-owned (``flock`` / ``msvcrt.locking``),
    so a helper that died releases it with its process table — no
    stale-lock timer, and no machine that can never update again. This is
    the server-side probe, which turns a second "update now" click into
    a 409; the helper takes the same lock for its whole run, which is
    what serialises two servers sharing one ``VIBE_HOME``.
    """
    path = updates_dir() / APPLY_LOCK_NAME
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            _lock(fd, non_blocking=True)
        except OSError:
            try:
                pid = os.read(fd, 32).decode('ascii', 'replace').strip()
            except OSError:
                pid = '?'
            return (
                f'another update is being applied (pid {pid}) — try '
                'again once it finishes'
            )
        return None
    finally:
        os.close(fd)


def _lock(fd: int, non_blocking: bool) -> None:
    """Take byte 0 of *fd* exclusively, for the lifetime of the process."""
    if is_windows_bundle():
        import msvcrt  # noqa: PLC0415 — Windows-only module

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(
            fd,
            msvcrt.LK_NBLCK if non_blocking else msvcrt.LK_LOCK,
            1,
        )
        return
    import fcntl  # noqa: PLC0415 — POSIX-only module

    mode = fcntl.LOCK_EX
    if non_blocking:
        mode |= fcntl.LOCK_NB
    fcntl.flock(fd, mode)


def read_record() -> dict | None:
    """``<VIBE_HOME>/updates/last.json``, or None."""
    try:
        doc = json.loads(record_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def record_mtime() -> float:
    """The record's mtime, or 0 when there is none.

    How an apply watcher notices a NEW record: mtime is a number that
    cannot tie, where a second-resolution timestamp in the document
    could."""
    try:
        return record_path().stat().st_mtime
    except OSError:
        return 0.0


def write_record(doc: dict) -> None:
    where = updates_dir()
    where.mkdir(parents=True, exist_ok=True)
    record_path().write_text(
        json.dumps(doc, indent=2, sort_keys=True), encoding='utf-8'
    )


def mark_record_reported() -> None:
    doc = read_record()
    if not doc or doc.get('reported'):
        return
    doc['reported'] = True
    write_record(doc)
