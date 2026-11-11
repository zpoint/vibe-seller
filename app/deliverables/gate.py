"""Refuse a report-download result that under-delivered.

Called directly from ``set_task_result`` rather than registered as a
skill-declared stop gate, because deciding what a run owed needs the
store row and the calendar — a DB session and a date, neither of which the sync gate
contract carries.

**The check engages on a declaration, and on nothing else.** The task
must have called ``vibe_seller_declare_report_run``
(:mod:`app.deliverables.declarations`) for at least one month, and it
then owes every derived file of every month it declared. Until now the
trigger was a folder scan — any ``reports_*`` directory in the workspace
turned a run into a report run — and that inferred an obligation from the
shape of the agent's own output: an ad-hoc "show me current Amazon sales"
that filed its CSVs under ``reports_sales_2026-11/`` was refused for a
missing monthly transaction report nobody had asked for. The project rule
is that no declaration means no inferred obligation, and a heuristic over
file names is exactly the inference the ad-task gates were rebuilt to
stop.

What is declared is only the MONTH. The expected file list stays derived
server-side from the store's declared capabilities and the calendar
(:mod:`app.deliverables.manifest`) — never from folder names, and never
from the run's own account of what it attempted, because a denominator
derived from the numerator can never be wrong. That is the mistake the
manifest exists for: a run once attempted 9 files of 19 and reported 9 of
9.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
from pathlib import Path

from app.ai.stop_gates import GateDeny
from app.browser.wrapper import store_slug
from app.deliverables.declarations import declared_months
from app.deliverables.manifest import (
    Deliverable,
    derive_manifest,
    month_before,
    store_capabilities,
)
from app.deliverables.verify import verify_workspace
from app.models.store import Store

GATE_NAME = 'deliverable_manifest'


def manifest_for_months(
    store, slug: str, months: list[str]
) -> list[Deliverable]:
    """Every file this store owes across *months*, in declaration order.

    One entry per path: two declared months can name the same late fee
    report (month ``M``'s storage file is owed by a run covering ``M``, and
    again by one covering ``M+1``), and owing it twice would only
    double-count the same gap.
    """
    out: dict[str, Deliverable] = {}
    for month in months:
        for entry in derive_manifest(
            store, slug, month, fee_month=month_before(month)
        ):
            out.setdefault(entry.relpath, entry)
    return list(out.values())


async def check_deliverables(
    db,
    task,
    task_root: Path,
    *,
    today: _dt.date | None = None,
) -> GateDeny | None:
    """Verify the workspace against the derived manifest.

    Returns ``None`` when this task declared no report run — an undeclared
    task simply is not a report run, whatever it filed where — or when
    everything expected is present with data, or when the only shortfalls
    are reports upstream has not published yet: a pending fee report is
    the normal state early in the month and must not read as a failure.
    Returns a :class:`GateDeny` naming each real gap otherwise; the
    framework retains the submission and, once the agent has had its one
    chance to fix, carries the gaps as caveats on a COMPLETED run.
    """
    if not getattr(task, 'store_id', None):
        return None

    months = await declared_months(db, task.id)
    if not months:
        return None

    store = await db.get(Store, task.store_id)
    if store is None:
        return None

    today = today or _dt.date.today()
    slug = store_slug(store.name, store.id)

    manifest = manifest_for_months(store, slug, months)
    if not manifest:
        return None

    # Reading the delivered spreadsheets (zip/xlsx parse) is blocking;
    # hand it to a thread so a big export cannot stall the loop.
    report = await asyncio.to_thread(
        verify_workspace,
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
            f'Deliverable check for the '
            f'{"month" if len(months) == 1 else "months"} you declared '
            f'({", ".join(months)}): {report.summary()}.\n\n'
            'A task that declares no run is never checked; this one '
            'declared. The expected file list is derived server-side from '
            "this store's declared capabilities and the calendar — not "
            'from what the run produced — so a file you did not attempt '
            'still counts as missing.\n\n'
            'A file that downloaded but holds only a header row counts as '
            'missing data, not as delivered: an unpublished monthly fee '
            'report arrives exactly that way.\n\n'
            + '\n'.join(f'- {d}' for d in detail)
        ),
        gaps=tuple(detail),
    )
