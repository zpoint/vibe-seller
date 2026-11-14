"""The release this machine applied and had to put back.

An apply that rolled back is ONE outage: the helper reinstalls the old
version, the server answers on it again, and the admin is told. The
update check has no memory of any of that — six hours later it reads the
same release, sees nothing about it changed, warms it, marks it ready,
and the idle loop applies it again. A release that cannot boot would
then cost a restart, some interrupted work and a rollback on every
single cycle, forever (review 4).

So the refusal lives in a file, not in a process: the helper that
decided to roll back is usually gone by the time the old version boots
again, and the booting process is the one that has to know.

Two rules decide its whole behaviour:

* a NEWER release clears it. Nothing re-arms the auto-apply by hand;
  the ordinary arrival of the next release does, which is what makes the
  memory safe to keep indefinitely.
* an OLDER one never does. Rolling back past a bad release must not make
  it eligible again — the question this answers is "has this machine
  moved on?", not "is this file older than something?".

A deliberate retry is still allowed: the refusal gates the idle timer,
never an admin's click (`app/auto_update.py`, ``manual_trigger``).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app import pip_update
from app.release_channel import is_newer

logger = logging.getLogger(__name__)

#: The file under ``<VIBE_HOME>/updates/`` holding the refusal. A
#: separate document from ``last.json`` on purpose: that one is
#: overwritten by the NEXT apply, and the reason to refuse is exactly
#: the outage that apply has not caused yet.
FILE_NAME = 'apply-blocked.json'


def path() -> Path:
    return pip_update.updates_dir() / FILE_NAME


def note_apply_result(record: dict | None) -> dict | None:
    """Remember the release an apply failed to leave running.

    Only a pip apply is remembered: an installer update is inert until
    clicked, so it cannot repeat anything, and a failure before the
    install (a prefetch that could not fetch) never caused an outage at
    all — that one is worth trying again on the next cycle.
    """
    if not record or record.get('path') != pip_update.PATH_PIP:
        return None
    if record.get('status') not in (
        pip_update.ROLLED_BACK,
        pip_update.STATUS_FAILED,
    ):
        return None
    to = record.get('to')
    if not to:
        return None
    entry = {
        'version': str(to),
        'status': record.get('status'),
        'stage': record.get('stage'),
        'error': record.get('error'),
        'at': record.get('at'),
        'from': record.get('from'),
    }
    try:
        where = path()
        where.parent.mkdir(parents=True, exist_ok=True)
        where.write_text(
            json.dumps(entry, indent=2, sort_keys=True), encoding='utf-8'
        )
    except OSError as exc:
        logger.warning('could not record the refused update: %s', exc)
        return None
    logger.warning(
        'v%s will not be auto-applied again (apply ended in %s at %s)',
        entry['version'],
        entry['status'],
        entry['stage'],
    )
    return entry


def current() -> dict | None:
    """The remembered release, or None."""
    try:
        doc = json.loads(path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and doc.get('version') else None


def applies_to(version: str | None) -> dict | None:
    """The refusal, when it is about *version*; else None.

    A release this machine has never tried is never refused: the memory
    names one version, and a release that cannot boot by now.
    """
    if not version:
        return None
    entry = current()
    return entry if entry and entry['version'] == version else None


def supersede(version: str) -> dict | None:
    """Clear the memory when the offer moved on; report what still stands.

    Returns the refusal that still applies to *version* — the one case
    being guarded against: the release arriving at ``ready`` IS the one
    this machine already put back.
    """
    entry = current()
    if entry is None:
        return None
    if version == entry['version']:
        return entry
    try:
        newer = is_newer(version, entry['version'])
    except Exception:  # noqa: BLE001 — an unreadable pin keeps the refusal
        logger.warning(
            'cannot compare v%s to the refused v%s — keeping the refusal',
            version,
            entry['version'],
        )
        return entry
    if newer:
        _clear()
        logger.info(
            'v%s supersedes the refused v%s — auto-apply is enabled again',
            version,
            entry['version'],
        )
        return None
    return entry


def _clear() -> None:
    try:
        path().unlink(missing_ok=True)
    except OSError as exc:
        logger.warning('could not clear the refused update: %s', exc)
