"""Which month a report-download run covers — declared before the work.

The sibling of :class:`app.models.ad_declaration.AdTaskDeclaration` and
the same idea: what a run owes is set by an explicit declaration the
agent makes, never inferred from the shape of what it produced.

**Why this exists at all.** The deliverable check used to engage whenever
the task workspace held any ``reports_*`` folder. An agent asked "show me
current Amazon sales" saved its CSVs into ``reports_sales_2026-11/`` and
the result was refused for a missing last-month transaction report — an
obligation nobody asked for, read out of the agent's own file naming. A
folder name says where a file was put, not what the user requested.

**What is declared is only the month.** The expected file list stays
server-derived (``app.deliverables.manifest``): the store's declared
capabilities and the calendar decide it, both of which are knowable
before the run starts. An agent-authored list would be the denominator
that once reported "9 of 9" against a set of 19.

**Append-only, and it can only add.** A declaration never removes an
obligation: the check covers every month this task has declared. So
re-declaring cannot be an escape, which is why there is no same-turn
ratchet here — the ratchet in ``tasks_ad_declaration`` exists because an
ad *kind* can lighten what a phase owes, and no month can.

**Scoped to a run, like the ad declaration.** ``POST /api/tasks/{id}/retry``
deletes these rows where it deletes the run's messages and
``AdTaskDeclaration`` rows: the promise was about a workspace that retry
wipes, so a surviving row would hold a fresh run to a month whose files can
no longer exist — the same false obligation, arriving from the other
direction.
"""

from datetime import UTC, datetime
import uuid

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ReportRunDeclaration(Base):
    """One declared report run. Never updated in place."""

    __tablename__ = 'report_run_declarations'

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    task_id: Mapped[str] = mapped_column(
        String,
        ForeignKey('tasks.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    # 1-based run number within the task, so a task that back-filled one
    # month and then ran the current one shows both, in order.
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    #: The calendar month the run covers, ``'YYYY-MM'``. Validated on
    # write (``app.deliverables.declarations``): a complete month that
    # has ended. The manifest is derived for exactly this value.
    month: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(UTC).isoformat()
    )
