"""Daily auto-delete of old terminal tasks.

Reads ``task_retention_days`` from AppSettings (default
``DEFAULT_TASK_RETENTION_DAYS``). 0 disables the job.

This is a system job; it never creates a Schedule row, so it
isn't visible in the user's schedules list.
"""

from datetime import UTC, datetime, timedelta
import logging

from app.database import async_session
from app.models.app_settings import AppSettings
from app.task_delete import delete_task, list_expired_task_ids

logger = logging.getLogger(__name__)

DEFAULT_TASK_RETENTION_DAYS = 30
TASK_RETENTION_KEY = 'task_retention_days'


async def _read_retention_days() -> int:
    async with async_session() as db:
        row = await db.get(AppSettings, TASK_RETENTION_KEY)
        if not row or not row.value:
            return DEFAULT_TASK_RETENTION_DAYS
        try:
            return int(row.value)
        except ValueError:
            return DEFAULT_TASK_RETENTION_DAYS


async def cleanup_old_tasks() -> int:
    """Delete completed/failed tasks older than the retention window.

    Returns the number of tasks deleted (useful for tests).
    """
    days = await _read_retention_days()
    if days <= 0:
        logger.debug('task_cleanup skipped: retention disabled (days=%s)', days)
        return 0

    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    deleted = 0
    async with async_session() as db:
        ids = await list_expired_task_ids(db, older_than_iso=cutoff)
    # Delete in fresh sessions to keep each rmtree + commit isolated.
    for tid in ids:
        async with async_session() as db:
            try:
                if await delete_task(db, tid):
                    deleted += 1
            except Exception:
                logger.exception('task_cleanup: failed to delete %s', tid)
    if deleted:
        logger.info(
            'task_cleanup: deleted %d task(s) older than %d day(s)',
            deleted,
            days,
        )
    return deleted
