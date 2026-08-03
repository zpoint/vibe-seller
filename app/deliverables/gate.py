"""Refuse a report-download result that under-delivered.

Called directly from ``set_task_result`` (the same shape as
``check_exec_review_status``) rather than registered as a skill-declared
stop gate, because deciding what a run owed needs the store row and the
calendar — a DB session and a date, neither of which the sync gate
contract carries.

Scope is deliberately narrow, and narrow in the fail-safe direction: the
check only engages for a store-scoped task whose workspace already holds
at least one ``reports_*`` folder. A task that downloaded no report
folders is not a report run and is left alone. This mirrors how the ad
completeness gate only bites a report presenting per-marketplace
sections — the failure mode is "we asked for less than we could have",
never "we invented an obligation".

The month is taken from the calendar, never from the folder names the
agent produced. Reading the expected set out of the agent's own output is
the mistake that let a run attempt 9 files and report 9 of 9: a
denominator derived from the numerator can never be wrong.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from app.ai.stop_gates import GateDeny
from app.browser.wrapper import store_slug
from app.deliverables.manifest import (
    derive_manifest,
    month_before,
    store_capabilities,
)
from app.deliverables.verify import verify_workspace
from app.models.store import Store

GATE_NAME = 'deliverable_manifest'


def _reporting_month(task, today: _dt.date) -> str:
    """The month a run started on *today* is expected to cover.

    The previous calendar month. Derived from the task's creation date
    when available so a re-run of an old task still checks the month that
    task was for, rather than whatever month it is now.
    """
    anchor = today
    created = getattr(task, 'created_at', None)
    if isinstance(created, str) and len(created) >= 7:
        try:
            anchor = _dt.date(int(created[0:4]), int(created[5:7]), 1)
        except ValueError:
            anchor = today
    return month_before(f'{anchor.year:04d}-{anchor.month:02d}')


def _looks_like_report_run(task_root: Path) -> bool:
    """True when the workspace holds any ``reports_*`` folder."""
    try:
        return any(
            p.is_dir() and p.name.startswith('reports_')
            for p in task_root.iterdir()
        )
    except OSError:
        return False


async def check_deliverables(
    db,
    task,
    task_root: Path,
    *,
    today: _dt.date | None = None,
) -> GateDeny | None:
    """Verify the workspace against the derived manifest.

    Returns ``None`` when everything expected is present with data, or
    when the only shortfalls are reports upstream has not published yet —
    a pending fee report is the normal state early in the month and must
    not read as a failure. Returns a :class:`GateDeny` naming each real
    gap otherwise; the framework retains the submission and, once the
    agent has had its one chance to fix, carries the gaps as caveats on a
    COMPLETED run.
    """
    if not getattr(task, 'store_id', None):
        return None
    if not _looks_like_report_run(task_root):
        return None

    store = await db.get(Store, task.store_id)
    if store is None:
        return None

    today = today or _dt.date.today()
    month = _reporting_month(task, today)
    slug = store_slug(store.name, store.id)

    manifest = derive_manifest(
        store, slug, month, fee_month=month_before(month)
    )
    if not manifest:
        return None

    report = verify_workspace(
        task_root,
        manifest,
        today=today,
        capabilities=store_capabilities(store),
    )
    if report.ok:
        return None

    # Pending entries ride along only when there is a real gap to report
    # beside them, so the agent sees the whole picture in one refusal
    # instead of chasing a file upstream has not posted.
    detail = list(report.gaps)
    if report.pending:
        detail += [
            f'(awaiting upstream, not your gap) {p}' for p in report.pending
        ]

    return GateDeny(
        gate=GATE_NAME,
        reason=(
            f'Deliverable check for {month}: {report.summary()}.\n\n'
            'The expected file list is derived server-side from this '
            "store's declared capabilities and the calendar — it is not "
            'read from what the run produced, so a file you did not '
            'attempt still counts as missing.\n\n'
            'A file that downloaded but holds only a header row counts as '
            'missing data, not as delivered: an unpublished monthly fee '
            'report arrives exactly that way.\n\n'
            + '\n'.join(f'- {d}' for d in detail)
        ),
        gaps=tuple(detail),
    )
