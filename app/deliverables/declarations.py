"""Reading and validating a declared report run.

The vocabulary the write path (``routers/tasks_report_declaration``) and
the read path (:mod:`app.deliverables.gate`) share, so the month a task
was allowed to declare is exactly the month the check then holds it to.

**The month is the only thing declared** — see
:mod:`app.models.report_declaration` for why declaring anything at all is
what replaced the ``reports_*`` folder heuristic, and
:mod:`app.deliverables.manifest` for why the file list is derived
server-side instead.

**Why the server picks the allowed months.** An agent that could name any
month could re-declare its way into whichever set of files it happened to
have collected. Restricting the value to a calendar month that has
actually ended keeps the declaration meaningful: whatever month is
chosen, a full month is owed, and a month that has not finished cannot be
delivered by anyone.
"""

from __future__ import annotations

import datetime as _dt
import re

from sqlalchemy import select

from app.deliverables.manifest import month_before
from app.models.report_declaration import ReportRunDeclaration

#: ``'2026-11'`` and nothing else — no day, no name, no single digit.
_MONTH = re.compile(r'^(\d{4})-(0[1-9]|1[0-2])$')


def parse_month(raw: object) -> str | None:
    """The month *raw* names in canonical form, or ``None`` if it names none.

    Surrounding space is trimmed because the value goes into a path and a
    string comparison, where ``' 2026-10'`` would silently mean a different
    folder than ``'2026-10'``.
    """
    text = str(raw or '').strip()
    return text if _MONTH.match(text) else None


def reporting_month(task, today: _dt.date) -> str:
    """The month a run started around *today* is expected to cover.

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


def month_error(month: str, expected: str, today: _dt.date) -> str | None:
    """Why *month* cannot be declared, or ``None`` when it can.

    Accepted: any complete calendar month, i.e. anything strictly before
    the one *today* sits in. That is the ordinary reporting month
    (*expected*, the month before the task was created) and, for an
    explicit back-fill, any month older. A back-fill costs the agent
    nothing it could use as an escape: the older month is a full set of
    files too, and its late-publishing fee reports have long been due, so
    the check on it is *stricter* — nothing can be excused as "awaiting
    upstream".

    Refused: the month still running, and anything in the future. Neither
    can be delivered as a complete month, so accepting one would either
    fail a run for data that does not exist yet or let a wrong month
    stand in for the one the user asked about. Refusals name *expected*,
    because an agent that guessed wrong needs the right value, not just
    a no.
    """
    current = f'{today.year:04d}-{today.month:02d}'
    if month > current:
        return (
            f'{month} is in the future. The reporting month for this task '
            f'is {expected}; declare that unless the user asked for a '
            'back-fill.'
        )
    if month == current:
        return (
            f'{month} has not ended yet, so no run can deliver it as a '
            f'complete calendar month. The reporting month for this task '
            f'is {expected}; an earlier month is accepted only for a '
            'back-fill the user asked for.'
        )
    return None


def format_hint(expected: str) -> str:
    """The one line that tells an agent what to send instead."""
    return f'month must be a calendar month like {expected} ("YYYY-MM").'


async def declared_months(db, task_id: str) -> list[str]:
    """Every month this task has declared a run for, oldest first.

    Distinct and in declaration order: a task that declared the same
    month twice owes it once, and a task that declared two months owes
    both. Reading the rows rather than the newest one is what makes
    re-declaring unable to shed an obligation.
    """
    rows = (
        await db.scalars(
            select(ReportRunDeclaration.month)
            .where(ReportRunDeclaration.task_id == task_id)
            .order_by(ReportRunDeclaration.seq.asc())
        )
    ).all()
    return list(dict.fromkeys(rows))


async def next_seq(db, task_id: str) -> int:
    """The seq the next declaration for *task_id* takes."""
    latest = await db.scalar(
        select(ReportRunDeclaration.seq)
        .where(ReportRunDeclaration.task_id == task_id)
        .order_by(ReportRunDeclaration.seq.desc())
        .limit(1)
    )
    return (latest or 0) + 1
