"""When the in-app update cannot get the release: send the admin the
manual route.

The background cycle finds a release, downloads it, and when the
download fails it simply goes round again six hours later. That part is
right and stays as it is. What was wrong is that nobody heard about it:
the error lived only in memory, the next failed CHECK reset it to idle,
and a restart forgot it. A machine whose network cannot reach the
release source then sat on its old version with a UI that said nothing
at all — two days and three failed attempts on a machine in the field,
with nothing about it on screen the whole time.

So a failed download is remembered on disk, here, and while it stands
the admin is shown the manual route this app used before it learned to
update itself: the installer download link on Windows, the
``vibe-seller upgrade`` command on macOS/Linux.

It stands until one of:

* a later download reaches ``ready`` — the in-app flow takes over again
  (``app/auto_update._mark_ready`` clears it);
* the running version is no longer older than the release that failed —
  the admin updated by hand, or the machine got there another way. That
  is checked on every read (:func:`standing`), so an update the admin
  ran by hand ends it without anyone touching the file.

A failed check is deliberately NOT a reason to forget it: a machine
that cannot download a release usually cannot reach the check either.

``manual_due`` (on ``GET /api/system/update-status``) is the server's
answer to "pop the manual guide now": a failure stands and no newer
attempt is under way (``idle`` or ``error``). While a retry downloads,
the chip shows its progress instead; if it fails, the guide is back.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import time

from app import pip_update
from app.release_channel import channel_of, is_newer
from app.update_check import NIGHTLY_PAGE_URL, RELEASES_PAGE_URL

logger = logging.getLogger(__name__)

#: Under ``<VIBE_HOME>/updates/``, beside ``last.json`` (what an APPLY
#: did) and ``apply-blocked.json`` (a release an apply put back). This
#: one is about the step before either: the bytes never arrived.
FILE_NAME = 'download-failed.json'

#: An exception message can carry a whole HTTP body; the dialog shows a
#: line, not a page.
MAX_ERROR_CHARS = 500

UPGRADE_COMMAND = 'vibe-seller upgrade'


def path() -> Path:
    return pip_update.updates_dir() / FILE_NAME


def route(platform: str, version: str) -> dict:
    """How a person updates to *version* by hand on *platform*.

    The one definition of the manual route: ``GET /update-check`` (the
    popup for installs that cannot update themselves) and the failure
    guide here hand out the same link and the same command.
    """
    if platform == 'windows':
        page = (
            NIGHTLY_PAGE_URL
            if channel_of(version) == 'nightly'
            else RELEASES_PAGE_URL
        )
        return {'upgrade_command': None, 'download_url': page}
    return {'upgrade_command': UPGRADE_COMMAND, 'download_url': None}


def read() -> dict | None:
    try:
        doc = json.loads(path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get('version'), str):
        return None
    return doc


def note(version: str, *, path_kind: str, stage: str, error: str) -> dict:
    """Remember that *version* could not be downloaded; return the entry.

    ``attempts`` counts the failures of THIS version: a newer release
    that also fails starts again at one. Blocking file I/O — callers on
    the event loop go through ``asyncio.to_thread``.
    """
    prior = read()
    attempts = 1
    if prior and prior.get('version') == version:
        attempts = int(prior.get('attempts') or 0) + 1
    now = time.time()
    entry = {
        'version': version,
        'path': path_kind,
        'stage': str(stage),
        'error': (error or 'the download failed')[:MAX_ERROR_CHARS],
        'attempts': attempts,
        'at': now,
        'at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now)),
    }
    try:
        where = path()
        where.parent.mkdir(parents=True, exist_ok=True)
        where.write_text(
            json.dumps(entry, indent=2, sort_keys=True), encoding='utf-8'
        )
    except OSError as exc:
        # The guide still shows for this process; only a restart would
        # forget it, which is the old behaviour, not a new failure.
        logger.warning('could not record the failed download: %s', exc)
    logger.warning(
        'update v%s could not be downloaded (attempt %d, %s path, stage '
        '%s): %s — the admin is shown the manual update route',
        version,
        attempts,
        path_kind,
        entry['stage'],
        entry['error'],
    )
    return entry


def standing(entry: dict | None, running: str) -> dict | None:
    """*entry* while it is still news to a machine on *running*.

    Pure, so ``auto_update.snapshot`` can ask it on every poll without
    touching the disk.
    """
    if not entry:
        return None
    try:
        return entry if is_newer(entry['version'], running) else None
    except Exception:  # noqa: BLE001 — an unreadable version is no news
        return None


def load(running: str) -> dict | None:
    """The record a previous boot left, or None — and a record the
    machine has since moved past is removed, not just ignored."""
    entry = read()
    if entry is not None and standing(entry, running) is None:
        logger.info(
            'v%s is no longer newer than v%s — dropping its download failure',
            entry.get('version'),
            running,
        )
        clear()
        return None
    return entry


def clear() -> None:
    try:
        path().unlink(missing_ok=True)
    except OSError as exc:
        logger.warning('could not clear the failed-download record: %s', exc)


def manual_due(state: str, entry: dict | None) -> bool:
    """Whether the admin should be sent to the manual route NOW.

    Only between attempts: while a retry is downloading, or one reached
    ``ready`` or is being applied, the in-app flow is the thing to
    watch."""
    return entry is not None and state in ('idle', 'error')
