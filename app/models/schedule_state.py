from datetime import UTC, datetime

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Sentinel store_id for cursors that don't belong to any specific
# store — i.e. non-fanout schedules whose fired tasks have no
# `store_id` (e.g. an email-sweep schedule). The PK includes
# store_id, so we need a non-NULL placeholder that round-trips
# through SQLAlchemy/SQLite cleanly.  Empty string is the cheapest
# choice and reads as "no store" without colliding with any real
# store UUID.  Used in both this model and ``tasks_schedule_state.py``.
NO_STORE_SCOPE = ''


class ScheduleState(Base):
    """Per-(schedule, store, key) cursor for scheduled-task agents.

    ``store_id`` is part of the primary key so a fanout schedule's
    sibling tasks each get an independent cursor — without it,
    whichever sibling writes last clobbers the others' state and
    later siblings short-circuit because they read a value that
    belongs to a different store. Real incident: a fanout schedule's
    demo-northshore task wrote ``last_report_date=2026-04-30`` and
    demo-meadowbrook's task on the same schedule then read that value
    and skipped its own download with "已下载".

    For non-fanout schedules whose tasks have no ``store_id``,
    callers pass ``NO_STORE_SCOPE`` (empty string) — semantically
    "schedule-level cursor".
    """

    __tablename__ = 'schedule_state'

    schedule_id: Mapped[str] = mapped_column(
        String,
        ForeignKey('schedules.id', ondelete='CASCADE'),
        primary_key=True,
    )
    # NOT NULL with empty-string sentinel — SQLite treats each NULL
    # in a composite PK as distinct, so allowing NULL would let
    # duplicate "(schedule_id, NULL, key)" rows accumulate while
    # being impossible to look up by equality. The sentinel keeps
    # the PK well-defined.
    store_id: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default=NO_STORE_SCOPE,
        primary_key=True,
    )
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(UTC).isoformat()
    )
    updated_by_task_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey('tasks.id', ondelete='SET NULL'),
        nullable=True,
    )
