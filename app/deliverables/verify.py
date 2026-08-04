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

from app.deliverables.manifest import Deliverable


class DeliverableStatus(enum.StrEnum):
    """Outcome for a single manifest entry."""

    OK = 'ok'
    MISSING = 'missing'
    #: Present, but nothing could be read out of it — a truncated
    #: download or an error page saved under a data file's name.
    UNREADABLE = 'unreadable'
    EMPTY = 'empty'
    #: Absent or empty, but the source is not published yet.
    PENDING = 'pending'
    #: Absent, and the capability that would require it is undeclared.
    SKIPPED = 'skipped'


#: An XLSX sheet row is ``<row .../>``; counting them beats parsing the
#: whole workbook and needs no third-party dependency at import time.
_XLSX_ROW = re.compile(rb'<row[ >]')

#: Bytes held back between reads so a ``<row`` split across a boundary is
#: still matched. One less than the pattern's fixed width: enough to
#: complete a straddling match, too few to contain a whole one (which
#: would then be counted twice).
_XLSX_ROW_CARRY = 4


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

    Streamed with a carry-over so a tag straddling a read boundary is not
    lost. Undercounting here is not a harmless approximation: it would
    report a populated export as empty and manufacture a gap against a
    file that is perfectly fine.
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
                    carry = b''
                    for chunk in iter(lambda: fh.read(65536), b''):
                        buf = carry + chunk
                        rows += len(_XLSX_ROW.findall(buf))
                        # Retain strictly fewer bytes than the pattern's
                        # length, so the tail can complete a match that
                        # spans the boundary but can never hold a whole
                        # match itself — which would double-count.
                        carry = buf[-_XLSX_ROW_CARRY:]
                    best = max(best, max(rows - 1, 0))
    except (zipfile.BadZipFile, OSError, KeyError):
        # Not a readable workbook. ``None``, not 0 — see ``data_rows``.
        return None
    return best


def data_rows(path: Path) -> int | None:
    """Data rows in *path*, or ``None`` when it cannot be parsed.

    ``None`` rather than 0, because the two claims are different and only
    one of them is evidence. A deliverable that legitimately has no rows
    still answers its question — the file was produced and it says
    "nothing happened". A file that cannot be parsed answers nothing.

    Collapsing both to 0 was safe only while every entry demanded at
    least one row. Once an EVENT entry declares ``min_rows=0``, a
    truncated download, a half-written file, or an HTML error page saved
    under a ``.csv`` name would all satisfy ``count >= 0`` and be
    reported OK.
    """
    try:
        if path.suffix.lower() in ('.xlsx', '.xlsm'):
            return _xlsx_rows(path)
        return _csv_rows(path)
    except (OSError, UnicodeDecodeError, csv.Error):
        return None


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

    @property
    def expected(self) -> int:
        """Entries this store actually owes.

        Excludes SKIPPED — a deliverable whose capability was never
        declared is not owed, so counting it would understate a run that
        did everything asked of it.
        """
        return sum(
            1
            for s in self.statuses.values()
            if s is not DeliverableStatus.SKIPPED
        )

    def summary(self) -> str:
        """One line the agent and the reader both understand.

        Reports the derived denominator rather than a self-declared one —
        the reason this module exists is that a run once said "9 of 9"
        about a set of 19.
        """
        return (
            f'{self.delivered}/{self.expected} expected files present '
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
            # Capability first, latency second. An undeclared capability
            # means we never established that this store can produce the
            # file at all, so it is not owed — reporting it as "awaiting
            # upstream" would be a claim about a report that may not
            # exist for this store, and it would pad the denominator.
            if not declared:
                statuses[entry.relpath] = DeliverableStatus.SKIPPED
            elif not due:
                statuses[entry.relpath] = DeliverableStatus.PENDING
                pending.append(
                    f'{entry.relpath} — {entry.month} not yet published '
                    f'upstream (expected from day {entry.published_from_day} '
                    'of the following month)'
                )
            else:
                statuses[entry.relpath] = DeliverableStatus.MISSING
                gaps.append(f'{entry.relpath} — missing')
            continue

        count = data_rows(path)
        if count is None:
            # Present but unparseable. Never OK, whatever ``min_rows``
            # says — there is no reading of this file that answers the
            # question it was collected to answer.
            statuses[entry.relpath] = DeliverableStatus.UNREADABLE
            gaps.append(f'{entry.relpath} could not be parsed')
            continue
        rows[entry.relpath] = count

        # ``min_rows`` alone decides sufficiency. An EVENT deliverable
        # declares ``min_rows=0`` because zero occurrences is a legal
        # answer, so this one comparison covers it — special-casing the
        # kind here instead would silently ignore a raised threshold on a
        # future EVENT entry that genuinely must carry rows.
        if count >= entry.min_rows:
            statuses[entry.relpath] = DeliverableStatus.OK
            continue

        # Short of what it owes. Whether that is "upstream has not posted
        # the month" or "this is wrong" is the distinction a size check
        # cannot make and a reviewer cannot make by eye.
        if not due:
            statuses[entry.relpath] = DeliverableStatus.PENDING
            pending.append(
                f'{entry.relpath} — downloaded but empty; {entry.month} '
                'not yet published upstream'
            )
        else:
            statuses[entry.relpath] = DeliverableStatus.EMPTY
            shortfall = (
                'has 0 data rows (header only)'
                if count == 0
                else f'has {count} data row(s), short of {entry.min_rows}'
            )
            gaps.append(
                f'{entry.relpath} — present but {shortfall}; '
                f'{entry.month} should have published by now'
            )

    return VerifyReport(
        statuses=statuses,
        rows=rows,
        gaps=tuple(gaps),
        pending=tuple(pending),
    )
