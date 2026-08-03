"""Check a task workspace against its manifest, by rows not bytes.

The bug this exists for: an unpublished monthly fee report downloads as a
valid CSV carrying its header and no data. Upstream calls it
``Complete``, the download succeeds, and it is 306 bytes — so a
``size > 0`` assertion passes it, which is exactly what a real run's
definition-of-done check did before shipping one.

So the only question worth asking is how many data rows a file has, and
:func:`data_rows` answers it for both CSV and XLSX without loading either
fully into memory.

The verdict deliberately separates two kinds of shortfall:

``gaps``
    Somebody should have produced this and did not. Real work is
    missing.
``pending``
    Upstream has not published it yet — the entry declares a
    ``published_from_day`` and today is before it. Nobody erred, and the
    run should complete while saying so.

Both travel to the agent as gate text, but only ``gaps`` mean the run
under-delivered. That split is what keeps a day-3 storage absence from
reading like a failure while a day-20 absence still does.
"""

from __future__ import annotations

import csv
import dataclasses
import datetime as _dt
import enum
from pathlib import Path
import re
import zipfile

from app.deliverables.manifest import Deliverable, DeliverableKind


class DeliverableStatus(enum.StrEnum):
    """Outcome for a single manifest entry."""

    OK = 'ok'
    MISSING = 'missing'
    EMPTY = 'empty'
    #: Absent or empty, but the source is not published yet.
    PENDING = 'pending'
    #: Absent, and the capability that would require it is undeclared.
    SKIPPED = 'skipped'


#: An XLSX sheet row is ``<row .../>``; counting them beats parsing the
#: whole workbook and needs no third-party dependency at import time.
_XLSX_ROW = re.compile(rb'<row[ >]')


def _csv_rows(path: Path) -> int:
    """Data rows in a CSV — total lines minus the header.

    Uses the csv module rather than counting newlines because a quoted
    field may legally contain one, and a product title with a newline in
    it would otherwise inflate an empty file into a populated one.
    """
    with path.open('r', encoding='utf-8-sig', newline='') as fh:
        reader = csv.reader(fh)
        count = -1  # header
        for _ in reader:
            count += 1
    return max(count, 0)


def _xlsx_rows(path: Path) -> int:
    """Largest data-row count across the workbook's sheets.

    A monthly export puts its real content on one sheet among several, so
    the maximum is the meaningful figure; summing would let a workbook of
    empty sheets with headers look populated.
    """
    best = 0
    try:
        with zipfile.ZipFile(path) as zf:
            sheets = [
                n
                for n in zf.namelist()
                if n.startswith('xl/worksheets/') and n.endswith('.xml')
            ]
            for name in sheets:
                with zf.open(name) as fh:
                    rows = 0
                    for chunk in iter(lambda: fh.read(65536), b''):
                        rows += len(_XLSX_ROW.findall(chunk))
                    best = max(best, max(rows - 1, 0))
    except (zipfile.BadZipFile, OSError, KeyError):
        return 0
    return best


def data_rows(path: Path) -> int:
    """Data rows in *path*, or 0 when unreadable.

    Unreadable counts as zero on purpose: a file we cannot parse is not
    evidence that the question was answered.
    """
    try:
        if path.suffix.lower() in ('.xlsx', '.xlsm'):
            return _xlsx_rows(path)
        return _csv_rows(path)
    except (OSError, UnicodeDecodeError, csv.Error):
        return 0


def _publication_due(entry: Deliverable, today: _dt.date) -> bool:
    """Has *entry*'s source had time to publish by *today*?

    True when the entry declares no latency at all, or when today is on
    or after ``published_from_day`` of the month following its reporting
    month. The comparison is on real dates rather than day-of-month alone
    so a two-month-old report is never excused by an early calendar day —
    the bug in the prose rule this replaces was exactly that it keyed off
    the day number and forgot which month was being asked for.
    """
    if entry.published_from_day is None:
        return True
    year, mon = (int(p) for p in entry.month.split('-'))
    due_year, due_mon = (year + 1, 1) if mon == 12 else (year, mon + 1)
    try:
        due = _dt.date(due_year, due_mon, entry.published_from_day)
    except ValueError:  # pragma: no cover — day clamped into range
        due = _dt.date(due_year, due_mon, 28)
    return today >= due


@dataclasses.dataclass(frozen=True)
class VerifyReport:
    """What the workspace holds against what it owed."""

    statuses: dict[str, DeliverableStatus]
    rows: dict[str, int]
    gaps: tuple[str, ...]
    pending: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """True when nothing is missing beyond upstream latency."""
        return not self.gaps

    @property
    def delivered(self) -> int:
        return sum(
            1 for s in self.statuses.values() if s is DeliverableStatus.OK
        )

    def summary(self) -> str:
        """One line the agent and the reader both understand.

        Reports the derived denominator rather than a self-declared one —
        the reason this module exists is that a run once said "9 of 9"
        about a set of 19.
        """
        return (
            f'{self.delivered}/{len(self.statuses)} expected files present '
            f'with data; {len(self.gaps)} gap(s), '
            f'{len(self.pending)} awaiting upstream publication'
        )


def verify_workspace(
    workspace: Path,
    manifest: list[Deliverable],
    *,
    today: _dt.date | None = None,
    capabilities: dict[str, bool] | None = None,
) -> VerifyReport:
    """Compare *workspace* against *manifest*.

    *capabilities* distinguishes "declared true" from "undeclared". Only a
    declared-true capability turns an absent file into a gap; undeclared
    leaves it optional, so a store whose FBA status nobody has recorded is
    not failed for a report it may not be able to produce. That is the
    fail-safe direction: an unasserted capability under-demands rather
    than inventing an obligation.
    """
    today = today or _dt.date.today()
    caps = capabilities or {}

    statuses: dict[str, DeliverableStatus] = {}
    rows: dict[str, int] = {}
    gaps: list[str] = []
    pending: list[str] = []

    for entry in manifest:
        path = workspace / entry.relpath
        due = _publication_due(entry, today)
        declared = entry.requires is None or caps.get(entry.requires) is True

        if not path.exists():
            if not due:
                statuses[entry.relpath] = DeliverableStatus.PENDING
                pending.append(
                    f'{entry.relpath} — {entry.month} not yet published '
                    f'upstream (expected from day {entry.published_from_day} '
                    'of the following month)'
                )
            elif not declared:
                statuses[entry.relpath] = DeliverableStatus.SKIPPED
            else:
                statuses[entry.relpath] = DeliverableStatus.MISSING
                gaps.append(f'{entry.relpath} — missing')
            continue

        count = data_rows(path)
        rows[entry.relpath] = count

        if count >= max(entry.min_rows, 1):
            statuses[entry.relpath] = DeliverableStatus.OK
            continue

        # Present but carrying no data. What that means is a property of
        # the deliverable, not of the file — which is the distinction a
        # size check cannot make and a reviewer cannot make by eye.
        if entry.kind is DeliverableKind.EVENT:
            statuses[entry.relpath] = DeliverableStatus.OK
            continue

        if not due:
            statuses[entry.relpath] = DeliverableStatus.PENDING
            pending.append(
                f'{entry.relpath} — downloaded but empty; {entry.month} '
                'not yet published upstream'
            )
        else:
            statuses[entry.relpath] = DeliverableStatus.EMPTY
            gaps.append(
                f'{entry.relpath} — present but has 0 data rows '
                f'(header only); {entry.month} should have published by now'
            )

    return VerifyReport(
        statuses=statuses,
        rows=rows,
        gaps=tuple(gaps),
        pending=tuple(pending),
    )
