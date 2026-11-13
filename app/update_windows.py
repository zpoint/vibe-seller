"""The Windows half of the update cycle: which path, and how it gets ready.

`app/auto_update.py` owns the state machine (``idle → downloading → ready →
restarting``, the refusal, the apply watcher) and the macOS/Linux cycle.
Windows needs a decision those platforms never make — whether a release can be
applied as a few MB of wheels or has to go through the ~700 MB installer — and
it needs the staging machinery behind the second answer, so both live here.

The two modules call each other's privates on purpose: `_mark_ready`,
`_mark_error` and `_update` are ONE state machine's verbs, and duplicating them
here would let the two halves disagree about what ``ready`` means. The import
is a cycle at module level and is only ever resolved at call time, which
`tests/unit/test_module_import_cycles.py` pins.
"""

from __future__ import annotations

import logging
import time

from app import (
    auto_update as au,
    bundle,
    pip_update,
    update_events,
    windows_update as wu,
)
from app.telemetry_events import UpdateStage

logger = logging.getLogger(__name__)

#: What a pip attempt ended as: ready to apply, go and stage the
#: installer instead, or stop for this cycle (PyPI does not have the
#: version yet — the next cycle retries).
PIP_READY = 'ready'
PIP_TO_INSTALLER = 'installer'
PIP_FAILED = 'failed'


def stage_installer(offer: dict, started: float) -> None:
    """Download (resumed) → verify → ``ready``. Runs in a worker thread."""
    version = offer['version']
    had_part = wu.staged_paths(version)[1].exists()

    def progress(done: int, total: int | None) -> None:
        au._update(
            state=au.DOWNLOADING,
            version=version,
            path=pip_update.PATH_INSTALLER,
            downloaded_bytes=done,
            total_bytes=total if total is not None else offer.get('size'),
        )

    try:
        wu.stage_installer(offer, progress=progress)
    except Exception as exc:
        stage = (
            UpdateStage.VERIFY
            if isinstance(exc, wu.VerifyError)
            else UpdateStage.DOWNLOAD
        )
        update_events.downloaded(
            'failed',
            stage=stage,
            path=pip_update.PATH_INSTALLER,
            to_version=version,
            seconds=time.monotonic() - started,
            resumed=had_part,
        )
        au._mark_error(str(exc))
        return
    update_events.downloaded(
        'ok',
        path=pip_update.PATH_INSTALLER,
        to_version=version,
        seconds=time.monotonic() - started,
        resumed=had_part,
    )
    au._mark_ready(version, pip_update.PATH_INSTALLER)


def pip_ready(version: str, started: float) -> str:
    """Warm the cache and prove the offline apply.

    Returns one of :data:`PIP_READY`, :data:`PIP_TO_INSTALLER` (fetchable
    but not installable offline: the release moved dependency bytes the
    bundle does not hold, which is the installer's job, not an error) or
    :data:`PIP_FAILED` (the state already says ``error``; retry next
    cycle).
    """
    au._update(
        state=au.DOWNLOADING,
        version=version,
        path=pip_update.PATH_PIP,
        downloaded_bytes=None,
        total_bytes=None,
        error=None,
    )
    try:
        pip_update.warm(version)
    except Exception as exc:
        update_events.downloaded(
            'failed',
            stage=UpdateStage.PREFETCH,
            path=pip_update.PATH_PIP,
            to_version=version,
            seconds=time.monotonic() - started,
        )
        au._mark_error(str(exc))
        return PIP_FAILED
    reason = pip_update.offline_apply_proven(version)
    if reason:
        logger.info('pip path unavailable for %s: %s', version, reason)
        update_events.downloaded(
            'installer',
            path=pip_update.PATH_PIP,
            to_version=version,
            seconds=time.monotonic() - started,
        )
        return PIP_TO_INSTALLER
    pip_update.pre_install_browser(version)
    update_events.downloaded(
        'ok',
        path=pip_update.PATH_PIP,
        to_version=version,
        seconds=time.monotonic() - started,
    )
    au._mark_ready(version, pip_update.PATH_PIP)
    return PIP_READY


def installer_path(offer: dict, started: float) -> None:
    version = offer['version']
    if wu.staged_installer(
        version,
        expected_sha=wu.offer_sha256(offer),
        expected_size=offer.get('size'),
    ):
        au._mark_ready(version, pip_update.PATH_INSTALLER)
        return
    au._update(state=au.DOWNLOADING, version=offer['version'])
    stage_installer(offer, started)


def path_for(offer: dict) -> str:
    """pip or installer, from the two bundle documents.

    Anything unknown — no asset on the release, no ``bundle.json`` in
    this install (0.3.1 and earlier), a release source that would not
    answer — is the installer path. It is the one that is always
    correct; the pip path is only allowed when both sides positively
    agree.
    """
    release = None
    if offer.get('bundle'):
        try:
            release = wu.fetch_bundle(offer)
        except Exception:  # noqa: BLE001 — unknown means installer
            logger.debug('could not read the release bundle', exc_info=True)
    installed = bundle.read_bundle(bundle.bundle_file())
    return (
        pip_update.PATH_PIP
        if bundle.pip_path_applies(release, installed)
        else pip_update.PATH_INSTALLER
    )


def cycle() -> None:
    """One Windows background cycle; runs inside a worker thread."""
    started = time.monotonic()
    # Through `au`, because the cycle's own module boundary is not
    # where a caller of this code declares its version.
    current = au.get_version()
    try:
        offer = wu.check_for_update()
    except Exception as exc:
        # Network/JSON failure must not lose a stage we already have.
        logger.warning('update check failed: %s', exc)
        offer = None
    if offer:
        if path_for(offer) == pip_update.PATH_PIP:
            outcome = pip_ready(offer['version'], started)
            if outcome != PIP_TO_INSTALLER:
                # Ready, or an error to retry next cycle. Only a release
                # the cache cannot install offline continues below.
                return
            installer_path(offer, started)
            return
        installer_path(offer, started)
        return
    # Nothing offered: a boot scan of a newer verified stage still wins
    # (offline boot, or the release source is down).
    boot = wu.newest_staged_version(current)
    if boot:
        au._mark_ready(boot, pip_update.PATH_INSTALLER)
        return
    au._mark_idle()
