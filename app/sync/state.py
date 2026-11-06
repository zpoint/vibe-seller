"""Reading and writing ``sync_state`` rows.

The ONE place that touches the ``sync_state`` table. Scheduler, jobs
and the API all go through here so a column rename has one fan-out and
so unit tests can substitute a fake without touching SQLAlchemy.
"""

from datetime import UTC, datetime
import json
import logging

from sqlalchemy import select

from app.database import async_session
from app.models.app_settings import AppSettings
from app.models.sync_state import SyncState

logger = logging.getLogger(__name__)

#: Status values a run may report. ``skipped`` and ``failed`` do not
#: move ``last_ok_at``.
STATUS_UPDATED = 'updated'
STATUS_UNCHANGED = 'unchanged'
STATUS_SKIPPED = 'skipped'
STATUS_FAILED = 'failed'
STATUSES = (
    STATUS_UPDATED,
    STATUS_UNCHANGED,
    STATUS_SKIPPED,
    STATUS_FAILED,
)

#: Outcomes that count as "the job did its work": the upstream probe
#: ran to completion. Both refresh ``last_ok_at`` — an unchanged probe
#: is a successful probe (see SyncState.last_ok_at).
OK_STATUSES = (STATUS_UPDATED, STATUS_UNCHANGED)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _loads(raw: str | None) -> dict:
    """Parse a ``detail`` JSON blob; ``{}`` when absent or corrupt."""
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def to_dict(row: SyncState) -> dict:
    """API-shaped view of one row."""
    return {
        'name': row.job_name,
        'last_run_at': row.last_run_at,
        'last_ok_at': row.last_ok_at,
        'version': row.version,
        'etag': row.etag,
        'status': row.status,
        'error': row.error,
        'needs_restart': bool(row.needs_restart),
        'detail': _loads(row.detail),
    }


async def get_row(job_name: str) -> SyncState | None:
    """The raw row for ``job_name``, or ``None``."""
    async with async_session() as db:
        return await db.get(SyncState, job_name)


async def get_dict(job_name: str) -> dict | None:
    """API-shaped state for ``job_name``, or ``None``."""
    row = await get_row(job_name)
    return to_dict(row) if row is not None else None


async def get_all() -> dict[str, dict]:
    """Every stored row, keyed by job name."""
    async with async_session() as db:
        rows = (await db.execute(select(SyncState))).scalars().all()
    return {row.job_name: to_dict(row) for row in rows}


async def record(
    job_name: str,
    *,
    status: str,
    error: str | None = None,
    version: str | None = None,
    etag: str | None = None,
    needs_restart: bool | None = None,
    detail: dict | None = None,
) -> dict:
    """Persist the outcome of one run and return the new state.

    ``version`` / ``etag`` / ``detail`` are only overwritten when the
    run reports them — a failed run must not erase the last known
    upstream version. ``etag=''`` (as opposed to ``None``) clears the
    stored validator: upstream answered 200 without one. ``needs_restart``
    is sticky: it is set when a gated skill changes and cleared only at
    boot (see ``app.sync.state.clear_restart_flags``).
    """
    async with async_session() as db:
        row = await db.get(SyncState, job_name)
        if row is None:
            row = SyncState(job_name=job_name, needs_restart=False)
            db.add(row)
        row.status = status
        row.error = error
        row.last_run_at = _now()
        if status in OK_STATUSES:
            row.last_ok_at = row.last_run_at
        if version is not None:
            row.version = version
        if etag is not None:
            row.etag = etag or None
        if needs_restart:
            row.needs_restart = True
        if detail is not None:
            row.detail = json.dumps(detail)
        row.updated_at = row.last_run_at
        await db.commit()
        return to_dict(row)


async def clear_restart_flags() -> None:
    """Clear every ``needs_restart`` flag (boot, after gates preload)."""
    async with async_session() as db:
        rows = (await db.execute(select(SyncState))).scalars().all()
        for row in rows:
            if row.needs_restart:
                row.needs_restart = False
                row.updated_at = _now()
        await db.commit()


# ── Job-specific bookkeeping (a free-form side channel) ────────────


async def load_bookkeeping(job_name: str) -> dict:
    """The job's ``detail`` blob ({} when absent).

    Provider presets keep their fetch bookkeeping (source URL and the
    like) here rather than in the catalog cache file, so the cache is
    only ever catalog data.
    """
    row = await get_row(job_name)
    return _loads(row.detail) if row is not None else {}


async def save_bookkeeping(job_name: str, **fields: object) -> dict:
    """Merge ``fields`` into the job's ``detail`` blob and persist it."""
    async with async_session() as db:
        row = await db.get(SyncState, job_name)
        if row is None:
            row = SyncState(job_name=job_name, needs_restart=False)
            db.add(row)
        merged = _loads(row.detail)
        merged.update(fields)
        row.detail = json.dumps(merged)
        row.updated_at = _now()
        await db.commit()
        return merged


# ── Toggles (AppSettings-backed, checked before every tick) ────────


async def read_toggle(key: str | None) -> bool:
    """Whether ``key`` allows a scheduled run.

    ``None`` (no toggle — knowledge, provider presets, ...) is always
    on. A missing row means on: the auto-sync ships enabled and the
    user opts out. Any DB error degrades to enabled — better to sync
    once on a transient failure than to silently stop pulling updates.
    """
    if not key:
        return True
    try:
        async with async_session() as db:
            row = await db.get(AppSettings, key)
            return row is None or row.value == 'true'
    except Exception:
        logger.debug('Failed to read toggle %s; defaulting to on', key)
        return True
