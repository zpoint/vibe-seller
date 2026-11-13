"""In-app auto-update: fetch the new release in the background, then
apply it — by itself when it is small and the machine is idle, or on a
click when it is not.

One state machine, owned by this module (``app/routers/system.py`` only
reads it): ``idle`` → ``downloading`` → ``ready`` → ``restarting``, or
``error`` with a message. The web UI polls
``GET /api/system/update-status``; ``POST /api/system/update/restart``
applies it now (admin only).

``ready`` means one of two things, and the state says which:

- **path ``pip``** — the release changes only the Python package, so it
  installs with uv over the venv already there: a few MB, already in the
  cache, and proven to need no network (``app/pip_update.py``). Nothing
  further is asked of anyone: the server restarts onto it the first time
  it is idle (``app/update_idle.py``). If a busy machine has had no idle
  moment for a day, the admin is asked instead — see below.
- **path ``installer``** — the release changes the bundled runtime
  (Python, Git, uv, the Claude Code CLI, Chromium) or this install
  cannot prove otherwise, so #244's flow stands: the ~700 MB installer
  is staged in the background and the admin clicks "重启并更新".

Which of the two applies is decided by a fingerprint, never by a
version number: the installer embeds what it bundled as
``{app}\\bundle.json``, the release publishes the same document, and
equal fingerprints mean the new wheels are the whole update
(``app/bundle.py``). macOS/Linux have no installer to fingerprint, so
they always take the pip path.

Support is install-shaped, not a wish: channel ``dev`` never; native
Windows must run from the installer bundle (``unins000.exe`` beside the
venv); macOS/Linux must be a uv tool (``uv-receipt.toml`` in
``sys.prefix``) with ``uv`` on PATH. pip / docker / source checkouts are
**not supported** — the popup keeps showing the copy-command /
download-link instructions. The kill switch ``VIBE_SELLER_AUTO_UPDATE``
(env_options) defaults to on, and to off under pytest/CI unless
explicitly enabled, mirroring telemetry.

Everything blocking (urllib, hashing, subprocess, uv, the file system)
runs through one ``asyncio.to_thread`` per operation — nothing here
touches the event loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import shutil
import sys
import threading
import time

import httpx

from app import (
    apply_launch,
    pip_update,
    update_events,
    update_idle,
    update_refusal,
    update_windows,
    windows_update as wu,
)
from app.env_options import Options
from app.release_channel import channel_of, is_newer
from app.telemetry_events import UpdateStage
from app.update_check import fetch_latest_pypi_version, fetch_nightly_upgrade
from app.version import get_version

logger = logging.getLogger(__name__)

#: First check this long after boot (the server has a life to get into
#: first), then one cycle per interval.
FIRST_CHECK_DELAY_SECONDS = 60
CHECK_INTERVAL_SECONDS = 6 * 3600

#: How often an in-flight apply looks for the helper's record. The apply
#: is the one moment the machine may be down, so this is fast; the
#: update_idle interval (a minute) is the opposite trade.
APPLY_WATCH_INTERVAL_SECONDS = 2.0

IDLE = 'idle'
DOWNLOADING = 'downloading'
READY = 'ready'
ERROR = 'error'
RESTARTING = 'restarting'

_lock = threading.Lock()
_state: dict = {
    'state': IDLE,
    'version': None,
    'path': None,
    'downloaded_bytes': None,
    'total_bytes': None,
    'error': None,
    'ready_since': None,
    'last_update': None,
    # The release an apply already put back, when that is the release on
    # offer. Kept in the state because `snapshot()` answers the UI's
    # every-two-seconds poll without touching the disk (review 4).
    'refusal': None,
}

#: The detached apply child + its watcher (both paths).
_apply_proc = None
_apply_task: asyncio.Task | None = None


def _update(**kw: object) -> None:
    with _lock:
        _state.update(kw)


def snapshot() -> dict:
    """The state as the UI sees it. No I/O — see ``_settle`` for what
    that buys and ``load_last_update`` for how the record gets in.

    ``auto_apply`` is the answer to "will this update happen by
    itself", and the pip path is only half of it: a release this
    machine already applied and had to put back is never applied by
    itself a second time (review 4), because every check cycle would
    otherwise offer it again and every idle minute would pay for it
    with another restart. The admin can still apply it — the click is
    a decision, the tick is not.
    """
    with _lock:
        st = dict(_state)
    refusal = st['refusal'] or None
    st['auto_apply'] = st['path'] == pip_update.PATH_PIP and not (
        refusal and refusal.get('version') == st['version']
    )
    # A refused release pops: it is ready, it will never happen by
    # itself, and the only person who can end that is the admin — who
    # has to be told what happened, not left guessing why nothing moved.
    st['popup_due'] = st['state'] == READY and (
        bool(refusal)
        or update_idle.popup_due(st['path'] or '', st['ready_since'])
    )
    return st


#: What identifies the apply whose refusal and report this process has
#: already produced — see :func:`load_last_update`.
_record_key = None


def reset_for_tests() -> None:
    """Back to a cold machine. Tests only."""
    global _apply_proc, _apply_task, _record_mtime
    # The record stamp is part of the cold machine: a test that resets
    # the state but keeps another test's mtime would have
    # `refresh_last_update` conclude nothing had been written since.
    _record_mtime = 0.0
    _record_key = None
    with _lock:
        _state.update({
            'state': IDLE,
            'version': None,
            'path': None,
            'downloaded_bytes': None,
            'total_bytes': None,
            'error': None,
            'ready_since': None,
            'last_update': None,
            'refusal': None,
        })
    _apply_proc = None
    _apply_task = None


#: The record file's mtime as of the last load. `record_mtime()` is the
#: cheap half of "has anything been written since we looked", and it is
#: what lets a status request pick up a record this process booted
#: without — see :func:`refresh_last_update`.
_record_mtime = 0.0


def load_last_update() -> dict | None:
    """The outcome of the last applied update, from ``<VIBE_HOME>``.

    Read at boot (off the loop) and kept in the state, so ``snapshot()``
    stays I/O-free and a server that restarted onto a rolled-back update
    reports it to whoever asks.
    """
    global _record_mtime, _record_key
    _record_mtime = pip_update.record_mtime()
    doc = pip_update.read_record()
    if doc is None:
        return None
    _update(last_update=doc)
    # Once per APPLY, not once per read. `refresh_last_update` can hand
    # this the same record again after `mark_record_reported` rewrites
    # the file, and the two things below are not idempotent-looking
    # operations: the refusal writes its own file and logs a warning,
    # and the report sends an event. Keyed on what identifies the apply
    # — when, to, and the outcome — rather than on the file's bytes,
    # which the report itself changes.
    key = (doc.get('at'), doc.get('to'), doc.get('status'))
    if key == _record_key:
        return doc
    _record_key = key
    # The refusal is remembered from the record rather than re-lived:
    # the process that rolled the update back is usually gone by now,
    # and the one booting on the old version is the one that must not
    # try the same release again an hour later.
    _update(refusal=update_refusal.note_apply_result(doc))
    report_unreported_failure(doc)
    return doc
    return doc


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


def _settle(**kw: object) -> None:
    """Update the state unless a restart is already in flight — the apply
    path owns the machine once it set ``restarting``, and a cycle that
    finished racing it must not flip the UI back. The apply path itself
    writes RESTARTING/ERROR directly.

    ``ready`` goes through :func:`_mark_ready` instead, so the 24 h clock
    cannot be set without being recorded."""
    if snapshot()['state'] == RESTARTING:
        return
    _update(**kw)


def _mark_ready(version: str, path: str) -> None:
    """Arrive at ``ready`` and start (or keep) the clock on it.

    A ready pip update needs no answer from anyone, so its popup stays
    off until a day of never-being-idle makes the ask worthwhile; an
    installer update is inert until clicked, so it asks right away
    (``app/update_idle.popup_due``)."""
    if snapshot()['state'] == RESTARTING:
        return
    record = update_idle.note_ready(version, path)
    # The release this machine put back, unless this offer passed it.
    refusal = (
        update_refusal.supersede(version)
        if path == pip_update.PATH_PIP
        else None
    )
    _update(
        state=READY,
        version=version,
        path=path,
        ready_since=record['since'],
        error=None,
        downloaded_bytes=None,
        total_bytes=None,
        refusal=refusal,
    )
    if refusal:
        logger.warning(
            'v%s is ready but will NOT be applied by itself: the last '
            'apply of it ended in %s at stage %s',
            version,
            refusal.get('status'),
            refusal.get('stage'),
        )


def _mark_idle() -> None:
    if snapshot()['state'] == RESTARTING:
        return
    update_idle.clear_ready()
    _update(
        state=IDLE,
        version=None,
        path=None,
        downloaded_bytes=None,
        total_bytes=None,
        error=None,
        ready_since=None,
        refusal=None,
    )


def _mark_error(message: str) -> None:
    _settle(state=ERROR, error=message, downloaded_bytes=None)


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


async def _cycle_unix() -> None:
    """One macOS/Linux background cycle (async; only the fetch is
    off-loop via to_thread).

    Always the pip path: there is no bundled runtime to change, and
    the target came from PyPI, so it exists there."""
    started = time.monotonic()
    async with httpx.AsyncClient() as client:
        target = await _pypi_target(client)
    if not target:
        _mark_idle()
        return
    st = snapshot()
    if st['state'] == READY and st['version'] == target:
        return  # already warm this process
    # Progress is indeterminate here (no byte counts).
    _update(
        state=DOWNLOADING,
        version=target,
        path=pip_update.PATH_PIP,
        downloaded_bytes=None,
        total_bytes=None,
        error=None,
    )
    try:
        await asyncio.to_thread(pip_update.warm, target)
        await asyncio.to_thread(pip_update.pre_install_browser, target)
        await asyncio.to_thread(pip_update.keep_rollback_warm)
    except Exception as exc:
        update_events.downloaded(
            'failed',
            stage=UpdateStage.PREFETCH,
            path=pip_update.PATH_PIP,
            to_version=target,
            seconds=time.monotonic() - started,
        )
        _mark_error(str(exc))
        return
    update_events.downloaded(
        'ok',
        path=pip_update.PATH_PIP,
        to_version=target,
        seconds=time.monotonic() - started,
    )
    _mark_ready(target, pip_update.PATH_PIP)


async def run_cycle() -> None:
    """One background update cycle. The watch loop owns the calls."""
    if snapshot()['state'] == RESTARTING:
        return
    if sys.platform == 'win32':
        await asyncio.to_thread(update_windows.cycle)
    else:
        await _cycle_unix()


async def watch() -> None:
    """The lifespan background loop: first check after ~60 s, then every
    6 h. Exits silently when the feature is off/unsupported."""
    await asyncio.to_thread(load_last_update)
    if not in_app_update():
        return
    await asyncio.sleep(FIRST_CHECK_DELAY_SECONDS)
    while True:
        try:
            await run_cycle()
        except Exception:
            logger.exception('auto-update cycle failed')
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


async def idle_watch(task_queue, agents) -> None:
    """Apply a ready pip update the first moment the server is idle.

    One check per minute, because the cheap thing is to wait and the
    expensive thing is to be wrong: an update that restarts the server
    while a task is running kills it.

    The invariant is held by ordering, not by luck. Dispatch is paused
    BEFORE the live-agent count that authorises the restart is read, so
    nothing can start in the gap between deciding and acting — and if an
    agent turned out to be live anyway (it started a moment before the
    pause), dispatch resumes and this goes around again. The installer
    path is never auto-applied: it is #244's click-to-apply flow, and
    only a click may run an installer.
    """
    if not in_app_update():
        return
    while True:
        await asyncio.sleep(update_idle.IDLE_CHECK_INTERVAL_SECONDS)
        try:
            verdict = await idle_tick(task_queue, agents)
        except Exception as exc:  # noqa: BLE001 — retry at the next tick
            logger.error('auto-apply tick failed, will retry: %s', exc)
            continue
        if verdict != 'applied':
            # The verdict is the whole answer to "why is this machine
            # still on the old version" — a question an operator, and
            # every end-to-end leg, asks of a log. `idle_tick` already
            # names the condition that blocked it (a live agent, a
            # queued task, a schedule about to fire, a refusal); saying
            # nothing here makes the log silent exactly where the
            # explanation lives.
            logger.info('auto-apply tick: %s', verdict)


async def idle_tick(task_queue, agents) -> str:
    """One look at the machine: apply if it is free, otherwise say why not.

    Returns what it decided, so the caller's loop and the tests ask the
    same question of the same code: `'applied'`, `'busy: <reason>'`,
    `'not ready'` (nothing pending, or the installer path, which only a
    click may run) or `'refused'` (another apply already holds the
    single-flight lock — nothing was touched, look again next tick).
    """
    st = snapshot()
    if st['state'] != READY or st['path'] != pip_update.PATH_PIP:
        return 'not ready'
    if not st['auto_apply']:
        # Another outage for a release this machine already put back is
        # not a retry, it is the same failure on a timer (review 4).
        refusal = st['refusal'] or {}
        return (
            f'refused: v{st["version"]} ended in {refusal.get("status")} '
            f'at stage {refusal.get("stage")} and will not be applied '
            'by itself again'
        )
    verdict = await update_idle.evaluate(agents)
    if not verdict.idle:
        return f'busy: {verdict.reason}'
    # The gate is the invariant, and it is held by ordering: dispatch is
    # paused BEFORE the live-agent count that authorises the restart is
    # read, so nothing can start in the gap between deciding and acting.
    task_queue.pause_dispatch()
    started = False
    try:
        if agents.live_agent_count():
            return 'busy: an agent started during the check'
        logger.warning(
            'applying v%s automatically — the server is idle', st['version']
        )
        await apply_restart(
            interrupted_tasks=0, trigger=pip_update.TRIGGER_AUTO_IDLE
        )
        started = True
        return 'applied'
    except pip_update.ApplyInProgress:
        # Somebody else is applying. It left the state alone; so do we.
        return 'refused'
    except Exception as exc:  # noqa: BLE001 — the loop retries next tick
        logger.error('auto-apply failed, will retry: %s', exc)
        raise
    finally:
        # Dispatch resumes on every route EXCEPT the one where the update
        # is actually on its way: resuming there would start queued work
        # in the seconds before the server goes down.
        if not started:
            task_queue.resume_dispatch()


# ── apply ────────────────────────────────────────────────────────────


def refresh_last_update() -> None:
    """Read the record again if it has been written since we last saw it.

    Why this exists: the apply helper brings the old version back up
    FIRST and writes ``last.json`` as its last act, so on a rolled-back
    update the server that recovers the machine starts about six seconds
    before there is anything for it to read. Boot was the only moment the
    record was consulted, so `update-status` answered
    `last_update: null` for a machine that had just failed an update and
    reverted it — and the admin who opens the page the next morning sees
    an idle server instead of the outage. Measured on the Windows host
    (§9 of win-e2e2.REPORT.md): `status: rolled_back`, `stage: health` on
    disk, nothing in the answer.

    So the account of an update is read when the UI asks for it, off the
    event loop, and only when the file's mtime moved — one `stat` per
    request, no re-read of an unchanged file, and ``snapshot()`` stays
    I/O-free. Reporting stays once-only: the event goes out the first
    time, and `mark_record_reported` is what rewrites the file, so a
    later refresh sees `reported` and says nothing again.

    Called from the status route through `asyncio.to_thread`, and never
    from `snapshot()` itself.
    """
    # `record_mtime()` is documented to answer 0.0 rather than raise.
    mtime = pip_update.record_mtime()
    if mtime == _record_mtime:
        return
    load_last_update()


def report_unreported_failure(doc: dict) -> None:
    """Turn the record a previous boot left behind into one report.

    The helper that writes a failure is not the process that shows it:
    it may have restarted the machine onto the old version, or the new
    one may have come up over a rolled-back attempt. So the record
    carries ``reported``, and whoever boots first sends the event and
    clears it. Nothing is reported for a successful update — that is
    what the restart itself says."""
    if doc.get('reported') or doc.get('status') == pip_update.STATUS_OK:
        return
    update_events.apply_failed(
        str(doc.get('stage') or UpdateStage.APPLY),
        RuntimeError(doc.get('error') or 'the update failed'),
        str(doc.get('path') or pip_update.PATH_PIP),
    )
    pip_update.mark_record_reported()


def _apply_windows_installer(version: str) -> None:
    # Full re-verification before launching an installer that stops the
    # server: a ready stage whose bytes went bad since (disk corruption,
    # a tampered file) must fail CLOSED here, not mid-install.
    staged = wu.staged_installer(version)
    if staged is None:
        raise RuntimeError('the staged installer is missing')
    wu.run_installer(staged, mode='silent')


def _read_record_and_mtime() -> tuple[dict | None, float]:
    """One thread hop for both halves of "did the helper say anything?"."""
    return pip_update.read_record(), pip_update.record_mtime()


async def _watch_apply(since: float) -> None:
    """Watch an apply this process cannot see finish.

    On the way to success the helper stops this server, so a still-live
    process with a NEW record means it gave up before stopping us, or
    rolled back and restarted the old version. Without this the UI would
    sit on "restarting" until its own timeout, on a machine that never
    moved."""
    global _apply_proc
    deadline = time.monotonic() + pip_update.APPLY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        await asyncio.sleep(APPLY_WATCH_INTERVAL_SECONDS)
        doc, mtime = await asyncio.to_thread(_read_record_and_mtime)
        if doc is not None and mtime > since:
            if doc.get('status') == 'ok':
                return  # this process is about to be gone
            error = str(doc.get('error') or 'the update failed')
            if doc.get('status') == pip_update.ROLLED_BACK:
                error = f'{error} — reverted to v{doc.get("from")}'
            update_events.apply_failed(
                str(doc.get('stage') or UpdateStage.APPLY),
                RuntimeError(error),
                str(doc.get('path') or pip_update.PATH_PIP),
            )
            _update(
                state=ERROR,
                error=error,
                version=doc.get('to'),
                path=doc.get('path'),
                last_update=doc,
                refusal=update_refusal.note_apply_result(doc),
            )
            return
        proc = _apply_proc
        if proc is not None and proc.poll() is not None:
            exc = RuntimeError(
                f'the update helper exited {proc.returncode} without reporting'
            )
            update_events.apply_failed(
                UpdateStage.APPLY, exc, pip_update.PATH_PIP
            )
            _update(state=ERROR, error=str(exc))
            return
    exc = RuntimeError('the update did not finish in time')
    update_events.apply_failed(UpdateStage.WATCH, exc, pip_update.PATH_PIP)
    _update(state=ERROR, error=str(exc))


async def apply_restart(
    interrupted_tasks: int,
    trigger: str = pip_update.TRIGGER_MANUAL,
) -> dict:
    """Restart onto the ready update. Caller checked 409/403 already.

    Two ways out, and both leave the machine serving:

    - **Windows, installer path**: launch the staged installer detached.
      Its ``PrepareToInstall`` stops the server/tray/browsers and its
      silent finish relaunches the tray. The server must NOT stop itself
      first — that is #244, unchanged.
    - **every pip apply** (Windows and macOS/Linux): hand the machine to
      the detached helper, which installs, restarts, health-checks and —
      if the new version will not come up — puts the old one back
      (``app/pip_apply_helper.py``). This process is expected to be
      stopped by it.

    The idle caller owns the dispatch pause it took before calling, and
    releases it if this raises; this function never touches the queue.
    """
    global _apply_proc, _apply_task
    st = snapshot()
    version = st['version']
    path = st['path'] or pip_update.PATH_INSTALLER
    update_events.restart_requested(
        trigger=trigger,
        interrupted_tasks=interrupted_tasks,
        from_version=get_version(),
        to_version=version,
        path=path,
    )
    if not version:
        raise RuntimeError('no update is staged')
    _update(state=RESTARTING, error=None)
    try:
        if path == pip_update.PATH_INSTALLER:
            await asyncio.to_thread(_apply_windows_installer, version)
        else:
            # Read the record's mtime BEFORE handing the machine over: a
            # helper that fails immediately writes one, and a "is it
            # fresh?" test made after the spawn would miss the only
            # report that matters.
            since = await asyncio.to_thread(pip_update.record_mtime)
            _apply_proc = await asyncio.to_thread(
                apply_launch.apply, version, trigger, path
            )
            _apply_task = asyncio.create_task(_watch_apply(since))
    except pip_update.ApplyInProgress as exc:
        # Somebody else already owns this machine's update. Nothing was
        # touched, so the state goes back to waiting rather than to
        # `error`, and the caller answers 409.
        logger.info('apply refused: %s', exc)
        _update(state=READY, error=None)
        raise
    except Exception as exc:
        stage = (
            UpdateStage.INSTALLER
            if path == pip_update.PATH_INSTALLER
            else UpdateStage.APPLY
        )
        update_events.apply_failed(stage, exc, path)
        _update(state=ERROR, error=str(exc))
        raise
    return {'state': RESTARTING, 'version': version, 'path': path}


def manual_trigger() -> str:
    """How to label a click. After a day of never being idle, a pip
    update's popup is what asked for this, and that is worth knowing.

    A REFUSED release pops for a different reason — it already failed
    once and nothing will apply it by itself again — so a click on it
    is a deliberate retry, not the popup's answer, and `popup_due`
    alone cannot tell the two apart."""
    st = snapshot()
    if st['path'] != pip_update.PATH_PIP or st['refusal']:
        return pip_update.TRIGGER_MANUAL
    if st['popup_due']:
        return pip_update.TRIGGER_MANUAL_AFTER_24H
    return pip_update.TRIGGER_MANUAL
