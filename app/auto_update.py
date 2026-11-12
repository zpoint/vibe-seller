"""In-app auto-update: fetch the new release in the background, restart on click.

One state machine, owned by this module (``app/routers/system.py`` only
reads it): ``idle`` → ``downloading`` → ``ready`` → ``restarting``, or
``error`` with a message. The web UI polls ``GET /api/system/update-status``
and offers "重启并更新" when the state is ``ready``;
``POST /api/system/update/restart`` applies it (admin only).

What "downloaded" means per platform:

- **Windows (installer bundle)**: ``VIBE_SELLER_RELEASES_URL``/GitHub
  offers ``VibeSeller-Setup.exe`` with size + sha256; it is staged under
  ``<VIBE_HOME>/updates/<version>/`` (resumable ``.part`` + Range —
  see ``app/windows_update.stage_installer``). Applying launches the
  staged installer detached with ``/SILENT`` (progress window only):
  the installer itself stops the server/tray/browsers
  (``PrepareToInstall``) and relaunches the tray. A verified stage on
  boot is ``ready`` immediately, no network needed.
- **macOS/Linux (uv tool install)**: the target version's wheels are
  prefetched into uv's shared cache (``uv tool run --from
  vibe-seller==<v> …``) without touching the installed tool — provable
  by ``UV_OFFLINE=1 uv tool upgrade`` afterwards. Applying runs the
  real ``vibe-seller upgrade`` (all of what it does — package,
  Chromium, Claude Code CLI) from the warm cache, then
  ``app/upgrade_restart.request_restart()`` re-execs this process onto
  the new install immediately: the admin already accepted that running
  tasks are interrupted (no drain).

Support is install-shaped, not a wish: channel ``dev`` never; native
Windows must run from the installer bundle (``unins000.exe`` beside the
venv); macOS/Linux must be a uv tool (``uv-receipt.toml`` in
``sys.prefix``) with ``uv`` on PATH. pip / docker / source checkouts
are **not supported** — the popup keeps showing the copy-command /
download-link instructions. The kill switch
``VIBE_SELLER_AUTO_UPDATE`` (env_options) defaults to on, and to off
under pytest/CI unless explicitly enabled, mirroring telemetry.

Everything blocking (urllib, hashing, subprocess, uv) runs through one
``asyncio.to_thread`` per operation — nothing here touches the event
loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

import httpx

from app import telemetry, upgrade_restart, windows_update
from app.config import VIBE_SELLER_DIR
from app.env_options import Options
from app.release_channel import channel_of, is_newer
from app.telemetry_events import TelemetryEvent, UpdateStage
from app.update_check import fetch_latest_pypi_version, fetch_nightly_upgrade
from app.version import get_version

logger = logging.getLogger(__name__)

#: First check this long after boot (the server has a life to get into
#: first), then one cycle per interval.
FIRST_CHECK_DELAY_SECONDS = 60
CHECK_INTERVAL_SECONDS = 6 * 3600

#: How long the Unix apply watcher waits for the detached
#: ``vibe-seller upgrade`` to change the installed version.
APPLY_TIMEOUT_SECONDS = 15 * 60

IDLE = 'idle'
DOWNLOADING = 'downloading'
READY = 'ready'
ERROR = 'error'
RESTARTING = 'restarting'

_lock = threading.Lock()
_state: dict = {
    'state': IDLE,
    'version': None,
    'downloaded_bytes': None,
    'total_bytes': None,
    'error': None,
}

#: The detached upgrade child + its watcher (Unix apply path).
_upgrade_proc: subprocess.Popen | None = None
_apply_task: asyncio.Task | None = None


def _update(**kw: object) -> None:
    with _lock:
        _state.update(kw)


def snapshot() -> dict:
    with _lock:
        return dict(_state)


def reset_for_tests() -> None:
    """Back to a cold machine. Tests only."""
    global _upgrade_proc
    with _lock:
        _state.update({
            'state': IDLE,
            'version': None,
            'downloaded_bytes': None,
            'total_bytes': None,
            'error': None,
        })
    _upgrade_proc = None


def enabled() -> bool:
    """The ``VIBE_SELLER_AUTO_UPDATE`` switch.

    Default on, but off under pytest/CI unless explicitly enabled —
    the same convention app.telemetry uses for its own switch."""
    raw = Options.AUTO_UPDATE.get().strip().lower()
    if raw in ('1', 'true', 'on'):
        return True
    if raw in ('0', 'false', 'off'):
        return False
    if 'pytest' in sys.modules or 'PYTEST_CURRENT_TEST' in os.environ:
        return False
    return not any(
        os.environ.get(name)
        for name in (
            'CI',
            'GITHUB_ACTIONS',
            'BUILDKITE',
            'GITLAB_CI',
            'CIRCLECI',
            'JENKINS_URL',
        )
    )


def supported() -> bool:
    """Whether this install can update itself in place at all.

    Shape-of-install detection — deliberately independent of the env
    switch, so ``update-status`` can tell the UI which experience to
    render even while the feature is switched off.
    """
    current = get_version()
    if channel_of(current) == 'dev':
        return False
    if sys.platform == 'win32':
        # Only the installer bundle may stage + relaunch itself: that
        # layout is what owns unins000.exe next to the venv.
        return (Path(sys.prefix).parent / 'unins000.exe').exists()
    # uv tool installs carry their receipt in the venv root — and the
    # upgrade itself IS `uv tool upgrade`, so uv must be on PATH.
    return (Path(sys.prefix) / 'uv-receipt.toml').exists() and shutil.which(
        'uv'
    ) is not None


def in_app_update() -> bool:
    return enabled() and supported()


# ── cycles ───────────────────────────────────────────────────────────


def _send_downloaded(
    outcome: str,
    *,
    stage: str | None = None,
    to_version: str | None,
    seconds: float,
    resumed: bool = False,
) -> None:
    try:
        telemetry.send(
            TelemetryEvent.UPDATE_DOWNLOADED,
            {
                'outcome': outcome,
                'stage': stage,
                'platform': sys.platform,
                'channel': channel_of(get_version()),
                'to_version': to_version,
                'duration_bucket': telemetry.duration_bucket(seconds),
                'resumed': resumed,
            },
        )
    except Exception:  # never break the update for an event
        logger.debug('update_downloaded send failed', exc_info=True)


def _settle(**kw: object) -> None:
    """Update the state unless a restart is already in flight — the
    apply path owns the machine once it set ``restarting``, and a
    cycle that finished racing it must not flip the UI back. The apply
    path itself writes RESTARTING/ERROR directly."""
    if snapshot()['state'] == RESTARTING:
        return
    _update(**kw)


def _mark_error(message: str) -> None:
    _settle(state=ERROR, error=message, downloaded_bytes=None)


def _stage_installer_windows(offer: dict, started: float) -> None:
    """Download (resumed) → verify → ``ready``. Runs in a worker thread."""
    version = offer['version']
    had_part = windows_update.staged_paths(version)[1].exists()

    def progress(done: int, total: int | None) -> None:
        _update(
            state=DOWNLOADING,
            version=version,
            downloaded_bytes=done,
            total_bytes=total if total is not None else offer.get('size'),
        )

    try:
        windows_update.stage_installer(offer, progress=progress)
    except Exception as exc:
        stage = (
            UpdateStage.VERIFY
            if isinstance(exc, windows_update.VerifyError)
            else UpdateStage.DOWNLOAD
        )
        _send_downloaded(
            'failed',
            stage=stage,
            to_version=version,
            seconds=time.monotonic() - started,
            resumed=had_part,
        )
        _mark_error(str(exc))
        return
    _send_downloaded(
        'ok',
        to_version=version,
        seconds=time.monotonic() - started,
        resumed=had_part,
    )
    _settle(
        state=READY,
        version=version,
        error=None,
        downloaded_bytes=None,
        total_bytes=None,
    )


def _cycle_windows() -> None:
    """One Windows background cycle; runs inside a worker thread."""
    started = time.monotonic()
    current = get_version()
    staged_ver: str | None = None
    try:
        offer = windows_update.check_for_update()
    except Exception as exc:
        # Network/JSON failure must not lose a stage we already have.
        logger.warning('update check failed: %s', exc)
        offer = None
    if offer:
        staged_ver = (
            offer['version']
            if windows_update.staged_installer(offer['version'])
            else None
        )
    if offer and staged_ver == offer['version']:
        _settle(
            state=READY,
            version=offer['version'],
            error=None,
            downloaded_bytes=None,
            total_bytes=None,
        )
        return
    if offer:
        _update(state=DOWNLOADING, version=offer['version'])
        _stage_installer_windows(offer, started)
        return
    # Nothing offered: a boot scan of a newer verified stage still wins
    # (offline boot, or the release source is down).
    boot = _boot_staged_version(current)
    if boot:
        _settle(state=READY, version=boot, error=None)
        return
    _settle(
        state=IDLE,
        version=None,
        downloaded_bytes=None,
        total_bytes=None,
        error=None,
    )


def _boot_staged_version(current: str) -> str | None:
    """Newest verified staged installer newer than *current*, or None."""
    root = windows_update.updates_root()
    try:
        names = [p.name for p in root.iterdir() if p.is_dir()]
    except OSError:
        return None
    newest: str | None = None
    for name in names:
        if not is_newer(name, current):
            continue
        if newest is not None and not is_newer(name, newest):
            continue
        if windows_update.staged_installer(name) is not None:
            newest = name
    return newest


async def _pypi_target(client: httpx.AsyncClient) -> str | None:
    """The version this uv-tool install should move to, per channel."""
    current = get_version()
    if channel_of(current) == 'stable':
        latest = await fetch_latest_pypi_version(client)
    else:
        latest = await fetch_nightly_upgrade(client, current)
    if not latest or not is_newer(latest, current):
        return None
    return latest


def _prefetch_unix(version: str) -> None:
    """Warm uv's wheel cache for *version* without touching the install.

    ``uv tool run`` resolves + downloads the pinned version and its
    dependencies into the shared cache and runs the fresh CLI's
    ``--version`` in an ephemeral env — the installed tool is not
    modified. The pin mirrors ``app/cli.py``'s nightly rule (an exact
    ``==`` of a pre-release is allowed without enabling pre-releases
    globally). Proof that the cache is warm: after this,
    ``UV_OFFLINE=1 uv tool upgrade vibe-seller`` succeeds."""
    cmd = [
        'uv',
        'tool',
        'run',
        '--from',
        f'vibe-seller=={version}',
        '--python',
        sys.executable,
        'vibe-seller',
        '--version',
    ]
    result = subprocess.run(  # noqa: S603
        cmd, capture_output=True, text=True, timeout=900
    )
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or '').strip()[-400:]
        raise RuntimeError(f'uv prefetch failed ({result.returncode}): {tail}')


async def _cycle_unix() -> None:
    """One macOS/Linux background cycle (async; only the fetch is
    off-loop via to_thread)."""
    started = time.monotonic()
    async with httpx.AsyncClient() as client:
        target = await _pypi_target(client)
    if not target:
        _settle(
            state=IDLE,
            version=None,
            downloaded_bytes=None,
            total_bytes=None,
            error=None,
        )
        return
    if snapshot()['state'] == READY and snapshot()['version'] == target:
        return  # already warm this process
    # Progress is indeterminate on these platforms (no byte counts).
    _update(
        state=DOWNLOADING,
        version=target,
        downloaded_bytes=None,
        total_bytes=None,
        error=None,
    )
    try:
        await asyncio.to_thread(_prefetch_unix, target)
    except Exception as exc:
        _send_downloaded(
            'failed',
            stage=UpdateStage.PREFETCH,
            to_version=target,
            seconds=time.monotonic() - started,
        )
        _mark_error(str(exc))
        return
    _send_downloaded(
        'ok', to_version=target, seconds=time.monotonic() - started
    )
    _settle(
        state=READY,
        version=target,
        error=None,
        downloaded_bytes=None,
        total_bytes=None,
    )


async def run_cycle() -> None:
    """One background update cycle. The watch loop owns the calls."""
    if snapshot()['state'] == RESTARTING:
        return
    if sys.platform == 'win32':
        await asyncio.to_thread(_cycle_windows)
    else:
        await _cycle_unix()


async def watch() -> None:
    """The lifespan background loop: first check after ~60 s, then every
    6 h. Exits silently when the feature is off/unsupported."""
    if not in_app_update():
        return
    await asyncio.sleep(FIRST_CHECK_DELAY_SECONDS)
    while True:
        try:
            await run_cycle()
        except Exception:
            logger.exception('auto-update cycle failed')
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


# ── apply ────────────────────────────────────────────────────────────


def _send_restart_requested(interrupted: int) -> None:
    try:
        bucket = (
            '0' if interrupted == 0 else ('1' if interrupted == 1 else '2+')
        )
        telemetry.send(
            TelemetryEvent.UPDATE_RESTART_REQUESTED,
            {
                'from_version': get_version(),
                'to_version': snapshot()['version'],
                'platform': sys.platform,
                'interrupted_tasks': bucket,
            },
        )
    except Exception:
        logger.debug('update_restart_requested send failed', exc_info=True)


def _send_apply_failed(stage: str, exc: BaseException) -> None:
    try:
        telemetry.send(
            TelemetryEvent.UPDATE_APPLY_FAILED,
            {
                'platform': sys.platform,
                'stage': stage,
                'error_class': type(exc).__name__,
            },
        )
    except Exception:
        logger.debug('update_apply_failed send failed', exc_info=True)


def _apply_windows(version: str) -> None:
    # Full re-verification before launching an installer that stops the
    # server: a ready stage whose bytes went bad since (disk corruption,
    # a tampered file) must fail CLOSED here, not mid-install.
    staged = windows_update.staged_installer(version)
    if staged is None:
        raise RuntimeError('the staged installer is missing')
    windows_update.run_installer(staged, mode='silent')


def _unix_upgrade_argv() -> list[str]:
    """How to run the REAL ``vibe-seller upgrade`` from this install —
    one source of truth for what an upgrade does (package + Chromium +
    Claude Code CLI, app/cli.py)."""
    script = Path(sys.executable).parent / 'vibe-seller'
    if script.exists():
        return [str(script), 'upgrade']
    return [sys.executable, '-I', '-m', 'app.cli', 'upgrade']


def _spawn_unix_upgrade() -> subprocess.Popen:
    log_dir = VIBE_SELLER_DIR / 'updates'
    log_dir.mkdir(parents=True, exist_ok=True)
    log = open(log_dir / 'upgrade.log', 'wb')  # noqa: SIM115 — the child inherits it; our copy closes below
    try:
        return subprocess.Popen(  # noqa: S603
            _unix_upgrade_argv(),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            cwd=str(log_dir),
        )
    finally:
        log.close()


def _upgrade_log_tail(limit: int = 400) -> str:
    try:
        text = (VIBE_SELLER_DIR / 'updates' / 'upgrade.log').read_text(
            encoding='utf-8', errors='replace'
        )
    except OSError:
        return ''
    return text.strip()[-limit:]


async def _watch_unix_upgrade(target: str) -> None:
    """Wait for the detached upgrade to land, then restart right away.

    The admin confirmed the interruption — no drain, no waiting for
    tasks. If the upgrade fails, the OLD version keeps serving and the
    state goes ``error``."""
    global _upgrade_proc
    loaded = get_version()
    deadline = time.monotonic() + APPLY_TIMEOUT_SECONDS
    proc = _upgrade_proc
    while time.monotonic() < deadline:
        on_disk = await asyncio.to_thread(upgrade_restart.installed_version)
        if on_disk and on_disk != loaded:
            logger.warning(
                'auto-update applied %s — restarting the server now', on_disk
            )
            upgrade_restart.request_restart()
            return
        if proc is not None and proc.poll() is not None:
            rc = proc.returncode
            on_disk = await asyncio.to_thread(upgrade_restart.installed_version)
            if on_disk and on_disk != loaded:
                upgrade_restart.request_restart()
                return
            exc = RuntimeError(
                f'vibe-seller upgrade exited {rc}: {_upgrade_log_tail()}'
            )
            _send_apply_failed('apply', exc)
            _update(state=ERROR, error=str(exc))
            return
        await asyncio.sleep(2)
    exc = RuntimeError('the upgrade did not finish in time')
    _send_apply_failed('watch', exc)
    _update(state=ERROR, error=str(exc))


async def apply_restart(interrupted_tasks: int) -> dict:
    """Restart onto the ready update. Caller checked 409/403 already.

    Windows: launch the staged installer detached — its
    ``PrepareToInstall`` stops the server/tray/browsers and its silent
    finish relaunches the tray. The server must NOT stop itself first.
    macOS/Linux: run the real upgrade from the warm cache, then
    ``upgrade_restart.request_restart()`` the moment the new version is
    installed on disk.
    """
    global _upgrade_proc, _apply_task
    _send_restart_requested(interrupted_tasks)
    st = snapshot()
    version = st['version']
    if not version:
        raise RuntimeError('no update is staged')
    _update(state=RESTARTING, error=None)
    try:
        if sys.platform == 'win32':
            await asyncio.to_thread(_apply_windows, version)
        else:
            _upgrade_proc = await asyncio.to_thread(_spawn_unix_upgrade)
            _apply_task = asyncio.create_task(_watch_unix_upgrade(version))
    except Exception as exc:
        stage = (
            UpdateStage.INSTALLER
            if sys.platform == 'win32'
            else UpdateStage.APPLY
        )
        _send_apply_failed(stage, exc)
        _update(state=ERROR, error=str(exc))
        raise
    return {'state': RESTARTING, 'version': version}
