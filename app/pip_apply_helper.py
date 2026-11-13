"""Apply an update from OUTSIDE the install it replaces, and prove it
came back — on every platform.

Run as a detached child of the server (``app/pip_update.py`` builds the
argument document) with an interpreter that is not the venv being
updated, on a copy of this file under ``<VIBE_HOME>/updates/``. Both
details matter: this process has to survive the server that spawned it
and keep working while the install is replaced underneath it, so it may
not be loaded from that install, nor import anything from it. Standard
library only, no ``app`` imports — pinned by
``tests/unit/test_pip_apply_helper.py``.

The reason it exists at all is the failure an update must never produce:
**a machine left with no server.** Applying an update is the one moment
the app is allowed to be down, and a release that crashes on boot would
otherwise take it down for good — crash-looping under launchd/systemd,
or simply gone under a plain daemon. So an apply is not done when the
files changed; it is done when the server answers on the new version.
Otherwise the old version is put back and answers instead:

1. learn what is installed now (the rollback target), THEN stop
   anything. On Windows this is a freeze of the venv; on macOS/Linux the
   uv-tool version, which ``uv tool install`` can name exactly.
2. install the target (Windows: the offline uv command the update check
   proved complete; macOS/Linux: the real ``vibe-seller upgrade``).
   A failure here left the server alone — record it and exit.
3. stop the server, start it again, and wait for ``/api/version`` to
   answer with the target. (The running server usually restarts itself
   onto the new install within seconds — ``app/upgrade_restart.py`` —
   so step 3 often finds it already up. Either way, the answer is the
   same question.)
4. if it never answers: reinstall the version from step 1, bring it back,
   and wait for it to answer. A rollback that cannot prove the old
   server is serving is reported as a plain failure, not as a success.
5. every exit path writes ``<VIBE_HOME>/updates/last.json`` — status,
   stage, error, who asked — for whichever server boots next to show.

Step 4 is the point of the module, and it is why the apply is a detached
helper rather than the server's own restart: whatever killed the new
version is still there when the supervisor relaunches it, and only a
process outside the tree can decide to put the old one back.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
import urllib.request

#: Stages, as recorded — a bounded vocabulary so a report can group
#: failures by where they happened.
STAGE_FREEZE = 'freeze'
STAGE_INSTALL = 'install'
STAGE_STOP = 'stop'
STAGE_START = 'start'
STAGE_HEALTH = 'health'
STAGE_ROLLBACK = 'rollback'

STATUS_OK = 'ok'
STATUS_ROLLED_BACK = 'rolled_back'
STATUS_FAILED = 'failed'

STOP_TIMEOUT = 90.0
#: How long to wait for the OS to release the install's files after the
#: last process using them exits (the same race that makes the Windows
#: installer retry its venv teardown).
RELEASE_TIMEOUT = 45.0
INSTALL_TIMEOUT = 1800.0
HEALTH_TIMEOUT = 300.0
#: How long a healthy NEW version is given to come up on its own before
#: this helper stops and starts the server itself.
SELF_RESTART_GRACE = 120.0
#: How long a restarted-old-version is given to answer before the record
#: says the machine is dark. Named so a test can shrink every wait in the
#: module by patching one thing.
RECOVERY_PROBE = 20.0
#: In supervised mode, how long to let the supervisor do its own restart
#: before this helper stops waiting on it. Short enough that a
#: mis-detected supervisor costs under a minute of darkness; long enough
#: that a real launchd/systemd relaunch is not raced against us.
SUPERVISOR_GRACE = 60.0


class AnotherApplyInProgress(RuntimeError):
    """Another apply holds ``<VIBE_HOME>/updates/apply.lock``.

    Raised before anything is touched, so the loser of the race leaves
    the machine exactly as it found it. The lock is held for the whole
    run by the winner and released by the OS when that process ends, so
    a crashed helper cannot wedge a machine permanently."""


class ApplyError(RuntimeError):
    """A step failed, with the stage it failed in for the record."""

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage


#: Held for the life of this process; the OS releases it on exit, which
#: is why there is no stale-lock timer to get wrong.
_lock_fd: int | None = None


def acquire_lock(path: str) -> None:
    """Take the single-flight lock, or raise :class:`AnotherApplyInProgress`.

    Two servers can share one ``VIBE_HOME`` (a test instance, a second
    install), and an auto-apply can race an admin's click; two helpers
    interleaving stop/install/start against one venv is how a machine
    ends up dark. Byte 0, advisory, per-process — the same primitive
    ``app/pip_update.apply_in_progress`` probes from the server side.
    """
    global _lock_fd
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if os.name == 'nt':
            import msvcrt  # noqa: PLC0415 — Windows-only

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl  # noqa: PLC0415 — POSIX-only

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise AnotherApplyInProgress(
            f'another update is applying (lock: {path})'
        ) from None
    os.ftruncate(fd, 0)
    os.write(fd, str(os.getpid()).encode('ascii'))
    try:
        os.fsync(fd)
    except OSError:
        pass
    _lock_fd = fd


def _release_lock() -> None:
    """Give the lock back (the OS would anyway when this process exits;
    this exists so a caller — and a test — can be explicit)."""
    global _lock_fd
    if _lock_fd is not None:
        try:
            os.close(_lock_fd)
        finally:
            _lock_fd = None


def _log(msg: str) -> None:
    print(f'{time.strftime("%H:%M:%S")} {msg}', flush=True)


# ── process boundaries (the only things a test replaces) ─────────────


def run(argv, timeout: float = 120.0, env: dict | None = None):
    """Run *argv*, returning the CompletedProcess. Never raises."""
    argv = [str(a) for a in argv]
    try:
        return subprocess.run(  # noqa: S603
            argv, capture_output=True, text=True, timeout=timeout, env=env
        )
    except OSError as exc:
        return subprocess.CompletedProcess(argv, 127, '', str(exc))
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ''
        if isinstance(out, bytes):
            out = out.decode('utf-8', 'replace')
        return subprocess.CompletedProcess(argv, 124, out, 'timed out')


def popen(argv, env: dict | None = None):
    """Start *argv* fully detached, so it outlives this helper."""
    argv = [str(a) for a in argv]
    kwargs = {
        'stdin': subprocess.DEVNULL,
        'stdout': subprocess.DEVNULL,
        'stderr': subprocess.DEVNULL,
        'close_fds': True,
        'env': env,
    }
    if os.name == 'nt':
        kwargs['creationflags'] = 0x00000008 | 0x00000200
    else:
        kwargs['start_new_session'] = True
    return subprocess.Popen(argv, **kwargs)  # noqa: S603


def venv_processes(app_dir: str) -> list[int]:
    """PIDs running an executable under ``{app}\\.venv`` (Windows).

    PowerShell + CIM because the standard library has no process list
    on Windows, and this has to see the tray (pythonw), the server and
    any browser-use daemon still holding a ``.pyd`` — none of which is a
    child of this helper.
    """
    root = str(Path(app_dir) / '.venv').replace("'", "''")
    ps = (
        'Get-CimInstance Win32_Process | Where-Object {'
        '$_.ExecutablePath -and $_.ExecutablePath.StartsWith('
        f"'{root}', [StringComparison]::OrdinalIgnoreCase)"
        '} | ForEach-Object { $_.ProcessId }'
    )
    done = run([
        'powershell.exe',
        '-NoProfile',
        '-NonInteractive',
        '-Command',
        ps,
    ])
    return [
        int(line)
        for line in (done.stdout or '').splitlines()
        if line.strip().isdigit()
    ]


def stop_processes(pids: list[int]) -> None:
    for pid in pids:
        run(['taskkill.exe', '/PID', str(pid), '/T', '/F'], timeout=30.0)


# ── the platform seam ────────────────────────────────────────────────


def is_windows(args: dict) -> bool:
    return args['platform'] == 'win32'


def learn_current(args: dict) -> dict:
    """What is installed right now, as far as a rollback is concerned.

    Windows freezes the venv: the bundle keeps its own wheel set
    (``{app}\\wheels``), so the exact previous state can be put back
    with no network at all. macOS/Linux record the uv-tool version,
    which ``uv tool install --force`` can name — warm in the cache if
    the update check cached it, else from the index.
    """
    if is_windows(args):
        done = run(
            uv_pip(args) + ['freeze', '--python', args['venv_python']],
            timeout=300.0,
        )
        text = (done.stdout or '').strip()
        if done.returncode != 0 or not text:
            raise ApplyError(
                STAGE_FREEZE,
                f'uv pip freeze failed ({done.returncode}): {_tail(done)}',
            )
        path = Path(args['updates_dir']) / 'freeze-before.txt'
        path.write_text(text + '\n', encoding='utf-8')
        return {'kind': 'freeze', 'path': str(path)}
    if not args.get('from_version'):
        raise ApplyError(STAGE_FREEZE, 'the current version is unknown')
    return {'kind': 'version', 'version': args['from_version']}


def install_target(args: dict) -> None:
    """Install the update.

    Windows runs the argv the update check dry-ran offline against this
    very venv — passed through verbatim, so the promise and the act
    cannot diverge. macOS/Linux run the real ``vibe-seller upgrade``,
    which is the whole contract of that command (package, Chromium,
    Claude Code CLI) from a cache the update check already warmed.
    """
    argv = list(args['install_command'])
    if not argv:
        raise ApplyError(STAGE_INSTALL, 'no install command was recorded')
    done = run(argv, timeout=INSTALL_TIMEOUT, env=env_for(args))
    if done.returncode != 0:
        raise ApplyError(
            STAGE_INSTALL,
            f'the install failed ({done.returncode}): {_tail(done)}',
        )


def rollback(args: dict, current: dict) -> None:
    """Put back what :func:`learn_current` found.

    Windows installs the freeze from the bundle's own wheels: offline,
    complete, no network. macOS/Linux reinstall the recorded version
    from the cache and fall back to the index — a recovery path that
    needs the network is worse than no update, but better than none.

    A package the new release ADDED is left behind on both: nothing
    imports it once the old version is back.
    """
    if current['kind'] == 'freeze':
        # ``--offline`` + the bundle's wheels, never ``--no-index``:
        # after the FIRST pip update the installed version was fetched
        # from the index into the cache and exists nowhere in
        # ``{app}\wheels``, so a no-index rollback could not resolve it
        # and the second failed update would find no way back
        # (review 2 MUST 1). The cache half is guaranteed by
        # ``pip_update.keep_rollback_warm``, which runs on every update
        # cycle — and an apply can only be reached through a cycle.
        argv = uv_pip(args) + ['install', '--python', args['venv_python']]
        argv += ['--offline']
        for source in find_links(args):
            argv += ['--find-links', source]
        argv += ['-r', current['path']]
    else:
        spec = f'vibe-seller=={current["version"]}'
        base = [args['uv'], 'tool', 'install', '--force']
        done = run([*base, '--offline', spec], env=env_for(args))
        if done.returncode == 0:
            return
        _log(f'offline rollback unavailable ({_tail(done)}); using the index')
        argv = [*base, spec]
    done = run(argv, timeout=INSTALL_TIMEOUT, env=env_for(args))
    if done.returncode != 0:
        raise ApplyError(
            STAGE_ROLLBACK,
            f'rollback failed ({done.returncode}): {_tail(done)}',
        )


def find_links(args: dict) -> list[str]:
    """Every local wheel source this install may resolve from.

    Mirrors ``pip_update.find_links_sources`` (this script may not
    import the package it is replacing, so the rule is spelled twice
    and pinned by tests on both sides): the environment's
    ``UV_FIND_LINKS`` first, then ``{app}\\wheels``.

    Order and repetition matter because ``--find-links`` REPLACES the
    environment variable instead of extending it — a command that named
    only the bundle's wheels could not see the source the update check
    had warmed, and would report a rollback failure that was really a
    visibility one.
    """
    sources: list[str] = []
    env = (os.environ.get('UV_FIND_LINKS') or '').strip()
    if env:
        sources += [part.strip() for part in env.split(',') if part.strip()]
    sources.append(str(Path(args['app_dir']) / 'wheels'))
    return sources


def uv_pip(args: dict) -> list[str]:
    """The `uv pip` prefix. The verb is always spelled by the caller:
    `uv pip freeze` and `uv pip install` share it, and a helper that
    returned the whole install command would turn a freeze into
    `uv pip install freeze`."""
    return [args['uv'], 'pip']


def stop_server(args: dict) -> None:
    """Ask the daemon to quit, then insist.

    On Windows the tray and any browser daemon must go too: one locked
    file fails the install, so the invariant is "nothing runs from the
    venv", not "the server said goodbye"."""
    run(
        [args['stop_command'][0], *args['stop_command'][1:]],
        timeout=STOP_TIMEOUT,
        env=env_for(args),
    )
    if not is_windows(args):
        return
    deadline = time.monotonic() + RELEASE_TIMEOUT
    while time.monotonic() < deadline:
        pids = venv_processes(args['app_dir'])
        if not pids:
            return
        _log(f'stopping {len(pids)} process(es) still holding the venv')
        stop_processes(pids)
        time.sleep(float(args.get('poll_seconds', 1.0)))
    raise ApplyError(
        STAGE_STOP,
        f'{len(venv_processes(args["app_dir"]))} process(es) are still '
        'running from the venv after the stop timeout',
    )


def start_server(args: dict) -> None:
    """Start the app, by the mechanism this platform uses.

    Windows starts the TRAY, not the server: the tray owns the server
    (that is its contract, and what the installer's silent upgrade does
    too), so restarting through it leaves one owner of the process.
    Elsewhere the daemon is started directly.

    WHO is allowed to start it is not this function's question. Under
    launchd ``KeepAlive`` or systemd ``Restart=`` the supervisor is meant
    to relaunch what we stopped, and starting a copy alongside it would
    put two servers on one port — one of them outside every log, limit
    and restart policy that unit defines. :func:`ensure_running` decides
    between waiting for that and doing it here; this function only knows
    how.
    """
    if is_windows(args):
        popen(
            [
                args['tray_python'],
                str(Path(args['app_dir']) / 'tray.py'),
            ],
            env=env_for(args),
        )
        return
    popen(args['start_command'], env=env_for(args))


def ensure_running(args: dict, want: str) -> str:
    """Bring the app up on *want*, and say WHO brought it up.

    Called only after the old process is stopped — the caller records
    `server` when the running process moved onto the new install by
    itself (app/upgrade_restart.py) before ever getting here.

    The answer is recorded, because "which mechanism restarted it" is
    exactly what an operator wants when the machine was dark for a
    minute: `supervisor` (launchd/systemd relaunched what we stopped),
    `helper` (we started it), `tray` (Windows, where the tray owns the
    server).

    The order says both what is trusted and what is doubted. A
    supervised machine is left to its supervisor FIRST, because starting
    a second copy under launchd means two servers on one port, one of
    them outside the unit that owns it, its logs and its limits. But the
    detection can be wrong — `XPC_SERVICE_NAME` is set for anything
    launched out of a GUI terminal — and the cost of trusting it forever
    is a machine serving nothing. So after `SUPERVISOR_GRACE` with the
    port still DARK, this starts the server itself. There is no second
    copy to race at that moment, because nothing is answering: a dark
    port is the proof that the supervisor is not coming.

    "Not `want` yet" is not that proof, and the two cases must not be
    conflated: a supervised server that is answering — slowly booting,
    crash-looping, or still serving the old version off a half-read
    install — is a server the supervisor HAS. Starting beside it is the
    bug, so this keeps waiting for it up to the health bound and lets
    the caller's health check turn "never got there" into the recorded
    failure. Dark is decided by `version_answered` returning nothing at
    all, not by the version being wrong.
    """
    if args.get('supervisor') and not is_windows(args):
        if wait_for_version(args, want, SUPERVISOR_GRACE):
            return 'supervisor'
        if version_answered(args):
            _log(
                f'supervisor {args["supervisor"]} has not reached {want} '
                f'within the grace (answering {version_answered(args)}) — '
                'the port is NOT dark, so the supervisor owns this '
                'process; waiting for it up to the health bound'
            )
            wait_for_version(args, want)
            return 'supervisor'
        _log(
            f'supervisor {args["supervisor"]} did not bring the server '
            'back within the grace and nothing answers the port; '
            'starting it here (a dark port is the proof there is no '
            'second copy to race)'
        )
        start_server(args)
        return 'helper'
    start_server(args)
    return 'tray' if is_windows(args) else 'helper'


def env_for(args: dict) -> dict:
    """The environment the app runs under, carried through from the
    server that spawned this helper: same VIBE_HOME, same port, same
    proxy settings. Not ``os.environ`` alone — the app must come back
    serving what went away."""
    env = dict(os.environ)
    env.update({k: str(v) for k, v in (args.get('env') or {}).items()})
    return env


def _tail(done) -> str:
    return ((done.stderr or '') + (done.stdout or '')).strip()[-400:]


def version_answered(args: dict) -> str:
    """The version the server reports, or '' when nothing answers.

    No proxy handler: a shell-level proxy must not intercept loopback
    and make a healthy server look dead (the same reason app/cli.py
    probes this way)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    url = f'http://127.0.0.1:{args["port"]}/api/version'
    try:
        with opener.open(url, timeout=3) as resp:  # noqa: S310
            return str(json.loads(resp.read().decode()).get('version') or '')
    except Exception:  # noqa: BLE001 — down, restarting, mid-write
        return ''


def wait_for_version(
    args: dict, want: str, timeout: float | None = None
) -> bool:
    """Poll until the server answers with *want*. True if it did.

    The interval comes from the argument document so a test can drive a
    whole apply without waiting on real seconds; 2 s is the production
    answer for a server that boots in seconds."""
    deadline = time.monotonic() + float(
        HEALTH_TIMEOUT if timeout is None else timeout
    )
    poll = float(args.get('poll_seconds', 2.0))
    while True:
        if version_answered(args) == want:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll)


# ── the record ───────────────────────────────────────────────────────


def write_record(
    args: dict,
    status: str,
    stage: str | None,
    error: str | None,
    started: float,
    error_class: str | None = None,
    who: str | None = None,
) -> dict:
    """``<VIBE_HOME>/updates/last.json`` — what happened, for whichever
    server boots next to show the admin and to report.

    ``reported`` is cleared by the server that turns a failure into a
    report, so one failure is counted once however many restarts it
    takes to recover."""
    doc = {
        'status': status,
        'from': args['from_version'],
        'to': args['target_version'],
        'path': args['path'],
        'trigger': args['trigger'],
        'stage': stage,
        'error_class': error_class,
        'error': error,
        'seconds': round(time.time() - started, 1),
        'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        # Who brought the server back. On a machine the operator
        # believes is supervised, `helper` here means the detection was
        # wrong and the safety net is what saved the update.
        'started_by': who,
        'reported': False,
    }
    path = Path(args['record'])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True), encoding='utf-8')
    return doc


def apply_update(args: dict) -> dict:
    """The whole sequence. Returns the record it wrote.

    Every exit path writes a record, and every exit path after the
    server was stopped leaves it RUNNING — on the new version if that
    came up, on the old one if it did not.

    The order differs by platform for one reason: on Windows the files
    are locked by the running server, so it must be stopped BEFORE the
    install can touch them. Elsewhere the install runs first, and the
    running server restarts itself onto it
    (``app/upgrade_restart.py``) — which this helper waits for, and
    does itself only if that did not happen.
    """
    started = time.time()
    stage = STAGE_FREEZE
    current: dict | None = None
    stopped = False
    attempted = False
    windows = is_windows(args)
    try:
        current = learn_current(args)
        if windows:
            stage = STAGE_STOP
            stop_server(args)
            stopped = True
        stage = STAGE_INSTALL
        # Marked before the call, not after: a half-run `uv pip install`
        # can leave the venv in a state that will not boot at all, which
        # is exactly when a rollback is needed. Rolling back an install that
        # failed before touching anything is merely a no-op.
        attempted = True
        install_target(args)
        # On Windows nothing is left running to bring itself back: the
        # helper stopped it to unlock the files.
        grace = 0.0 if windows else SELF_RESTART_GRACE
        who = 'server'
        if not wait_for_version(args, args['target_version'], grace):
            if not stopped:
                stage = STAGE_STOP
                stop_server(args)
                stopped = True
            stage = STAGE_START
            who = ensure_running(args, args['target_version'])
            stage = STAGE_HEALTH
            if not wait_for_version(args, args['target_version']):
                raise ApplyError(
                    STAGE_HEALTH,
                    f'the server never reported '
                    f'{args["target_version"]} (last answer: '
                    f'{version_answered(args) or "nothing"})',
                )
        return write_record(args, STATUS_OK, None, None, started, who=who)
    except BaseException as exc:  # noqa: BLE001 — every failure is recorded
        stage, error = stage, (str(exc) or type(exc).__name__)
        kind = type(exc).__name__
        _log(f'FAILED in {stage}: {error}')
        status, rolled_back = STATUS_FAILED, False
        if attempted and current is not None:
            try:
                stage = STAGE_ROLLBACK
                rollback(args, current)
                rolled_back = True
                status = STATUS_ROLLED_BACK
            except BaseException as back:  # noqa: BLE001 — both are recorded
                error = f'{error}; then the rollback failed too: {back}'
                kind = f'{kind}+{type(back).__name__}'
        if stopped or rolled_back:
            # Whatever happened, bring the app back on the version that
            # is on disk now — the rollback put the old one back, or the
            # install never ran and it is still the old one.
            try:
                if not wait_for_version(
                    args, args['from_version'], RECOVERY_PROBE
                ):
                    ensure_running(args, args['from_version'])
                if not wait_for_version(args, args['from_version']):
                    _log(f'v{args["from_version"]} did not come back')
            except BaseException:  # noqa: BLE001 — the real failure is recorded
                _log(
                    f'could not restart the old version:\n{traceback.format_exc()}'
                )
        return write_record(args, status, stage, error, started, kind)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print('usage: pip_apply.py <args.json>', file=sys.stderr)
        return 2
    args = json.loads(Path(argv[1]).read_text(encoding='utf-8'))
    try:
        acquire_lock(args['lock'])
    except AnotherApplyInProgress as exc:
        _log(f'not applying: {exc}')
        write_record(
            args,
            STATUS_FAILED,
            'lock',
            str(exc),
            time.time(),
            type(exc).__name__,
        )
        return 1
    _log(
        f'applying {args["from_version"]} -> {args["target_version"]} '
        f'({args["path"]}, {args["trigger"]})'
    )
    record = apply_update(args)
    _log(
        record['status']
        + (f': {record["error"]}' if record.get('error') else '')
    )
    return 0 if record['status'] == STATUS_OK else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
