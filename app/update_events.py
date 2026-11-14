"""What the in-app update reports about itself.

Split out of ``app/auto_update.py`` (which is a state machine and at its
line limit) because this half is a different job: it turns transitions
into events. Nothing here decides anything.

Privacy rules are #244's, unchanged: buckets, enums, channels and version
strings only. No paths, no host names, no store or campaign identifiers.
A failure reports a STAGE name and an exception CLASS, never the message
— an install error quotes a filesystem path.

Every function takes what it needs as arguments rather than reading
``app.auto_update``'s state, so the dependency only runs one way.
"""

from __future__ import annotations

import logging
import sys

from app import telemetry
from app.telemetry_events import TelemetryEvent, UpdatePath

logger = logging.getLogger(__name__)

#: The two dimensions the rollout is measured on: which mechanism did
#: the work, and who asked for it.
BUCKET_NONE = '0'
BUCKET_ONE = '1'
BUCKET_MANY = '2+'


def _bucket(interrupted_tasks: int) -> str:
    if interrupted_tasks == 0:
        return BUCKET_NONE
    if interrupted_tasks == 1:
        return BUCKET_ONE
    return BUCKET_MANY


def downloaded(
    outcome: str,
    *,
    path: str,
    to_version: str | None,
    seconds: float,
    stage: str | None = None,
    resumed: bool = False,
    channel: str | None = None,
) -> None:
    """One attempt to get the update ready.

    ``outcome`` is ``ok`` / ``failed`` / ``installer`` — the last one
    says the release was fetched but could not be installed offline,
    which is the measurement that tells us how often the cache is not
    enough and the ~700 MB installer has to run.
    """
    if channel is None:
        from app.release_channel import channel_of  # noqa: PLC0415
        from app.version import get_version  # noqa: PLC0415

        channel = channel_of(get_version())
    try:
        telemetry.send(
            TelemetryEvent.UPDATE_DOWNLOADED,
            {
                'outcome': outcome,
                'stage': stage,
                'path': path,
                'platform': sys.platform,
                'channel': channel,
                'to_version': to_version,
                'duration_bucket': telemetry.duration_bucket(seconds),
                'resumed': resumed,
            },
        )
    except Exception:  # never break the update for an event
        logger.debug('update_downloaded send failed', exc_info=True)


def restart_requested(
    *,
    trigger: str,
    interrupted_tasks: int,
    from_version: str,
    to_version: str | None,
    path: str | None,
) -> None:
    """Someone — the idle watcher or an admin — started an apply."""
    try:
        telemetry.send(
            TelemetryEvent.UPDATE_RESTART_REQUESTED,
            {
                'from_version': from_version,
                'to_version': to_version,
                'platform': sys.platform,
                'path': path or UpdatePath.INSTALLER,
                'trigger': trigger,
                'interrupted_tasks': _bucket(interrupted_tasks),
            },
        )
    except Exception:
        logger.debug('update_restart_requested send failed', exc_info=True)


def apply_failed(stage: str, exc: BaseException, path: str) -> None:
    """An apply that did not land.

    The exception is reduced to its class name on purpose: the message
    of an install failure is a filesystem path.
    """
    try:
        telemetry.send(
            TelemetryEvent.UPDATE_APPLY_FAILED,
            {
                'platform': sys.platform,
                'stage': stage,
                'path': path,
                'error_class': type(exc).__name__,
            },
        )
    except Exception:
        logger.debug('update_apply_failed send failed', exc_info=True)
