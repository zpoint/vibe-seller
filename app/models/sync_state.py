"""Persistent state for the central sync scheduler.

One row per sync job (``app.sync``). This table replaces the schedule
bookkeeping that used to live in per-module files (``.sync_meta.json``,
the provider-presets cache header): when a job last ran, when it last
succeeded, what upstream version it saw, and whether a synced change
needs a server restart to take effect.

Inventory is NOT here — the skills ``.sync_meta.json`` keeps its
``synced_skills`` / ``gws_installed`` / ``installed_deps`` keys, which
describe what is on disk rather than when something was polled.
"""

from datetime import UTC, datetime

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SyncState(Base):
    """Last-known state of one sync job."""

    __tablename__ = 'sync_state'

    #: Job name (``skills``, ``knowledge``, ``presets``, ...).
    job_name: Mapped[str] = mapped_column(String, primary_key=True)
    #: ISO timestamp of the last attempt (any outcome).
    last_run_at: Mapped[str | None] = mapped_column(String, nullable=True)
    #: ISO timestamp of the last successful run (updated or unchanged).
    #: The scheduler's next fire is computed from THIS, so a probe that
    #: finds nothing new still resets the clock (the old file-based
    #: cooldown forgot to, and every task launch then hit GitHub).
    last_ok_at: Mapped[str | None] = mapped_column(String, nullable=True)
    #: Upstream version last seen — commit sha for skills/knowledge,
    #: body sha256 for provider presets.
    version: Mapped[str | None] = mapped_column(String, nullable=True)
    #: HTTP validator for the last probe (sent back as If-None-Match).
    etag: Mapped[str | None] = mapped_column(String, nullable=True)
    #: Outcome of the last run: updated / unchanged / skipped / failed.
    status: Mapped[str | None] = mapped_column(String, nullable=True)
    #: Failure detail of the last run.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: A gated skill changed on disk but its gates are only loaded at
    #: boot — surfaced by ``GET /api/sync/status`` until the restart.
    needs_restart: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    #: JSON blob of job-specific bookkeeping that has no column of its
    #: own (per-run file counts, the presets source URL, ...). Legacy
    #: ``.sync_meta.json`` keys land here when imported (see
    #: ``app.sync.migrate``).
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[str] = mapped_column(
        String, nullable=False, default=_now
    )
