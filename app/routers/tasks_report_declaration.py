"""Declare that this task is a monthly report run, before the downloads.

Backs the ``vibe_seller_declare_report_run`` MCP tool. Split out of
``app/routers/tasks.py`` the way ``tasks_ad_declaration.py`` was, so no
file drifts over the repo's line cap.

Why a declaration and not a folder scan: the deliverable check used to
engage whenever the workspace held any ``reports_*`` directory, so an
ad-hoc sales lookup that filed its CSVs under ``reports_sales_2026-11/``
was refused for a missing monthly transaction report. That inferred an
obligation from how the agent named its own files — the move the ad
declarations exist to forbid. Now a run is checked because the agent said
it was running, and nothing else.

Why the agent declares only the month: what a run owes is derivable
without asking anybody — the store's declared capabilities and the
calendar (:mod:`app.deliverables.manifest`). An agent-authored list would
be the denominator that once reported "9 of 9" against a set of 19.

**Append-only, and it can only add.** Every declared month is owed, so
re-declaring can shed nothing and there is no same-turn ratchet to
enforce here (unlike ``tasks_ad_declaration``, where a kind change can
lighten what a phase owes). The answer repeats the file list the check
will use, so the agent is never working against a rule it cannot see.
"""

import datetime as _dt
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.browser.wrapper import store_slug
from app.database import get_db
from app.deliverables import declarations
from app.deliverables.gate import manifest_for_months
from app.models.report_declaration import ReportRunDeclaration
from app.models.store import Store
from app.models.task import Task
from app.models.user import User

router = APIRouter(prefix='/api/tasks', tags=['tasks'])


class DeclareReportRunRequest(BaseModel):
    # Free-shaped on the wire, validated server-side: an agent that sends
    # ``2026-1`` or ``last month`` is told the exact form rather than
    # silently recorded under a value the manifest cannot derive from.
    month: str | None = None


@router.get('/{task_id}/report-declarations')
async def list_report_declarations(
    task_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
):
    """Every report run this task has declared, oldest first."""
    rows = (
        await db.scalars(
            select(ReportRunDeclaration)
            .where(ReportRunDeclaration.task_id == task_id)
            .order_by(ReportRunDeclaration.seq.asc())
        )
    ).all()
    return [
        {
            'seq': row.seq,
            'month': row.month,
            'created_at': row.created_at,
        }
        for row in rows
    ]


@router.post('/{task_id}/report-declaration')
async def declare_report_run(
    task_id: str,
    body: DeclareReportRunRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
):
    """Record that this task is running the monthly reports for one month."""
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail='Task not found')

    # The manifest comes from a store row: platform countries, capability
    # flags, the slug the folders are named after. A store-less task — an
    # orchestrator, a finalize run — has no such list, so a declaration
    # here could only ever be checked against nothing.
    if not task.store_id:
        raise HTTPException(
            status_code=400,
            detail=(
                'A report run belongs to one store: the expected file '
                "list is derived from the store's own capabilities. This "
                'task has no store. Declare it on the per-store task that '
                'downloads the files — the folder layout, and the check, '
                "live in that task's workspace."
            ),
        )

    today = _dt.date.today()
    month = declarations.parse_month(body.month)
    if month is None:
        raise HTTPException(
            status_code=400,
            detail=declarations.format_hint(
                declarations.reporting_month(task, today)
            ),
        )

    expected = declarations.reporting_month(task, today)
    error = declarations.month_error(month, expected, today)
    if error:
        raise HTTPException(status_code=400, detail=error)

    seq = await declarations.next_seq(db, task_id)
    row = ReportRunDeclaration(task_id=task_id, seq=seq, month=month)
    db.add(row)
    await db.commit()
    await db.refresh(row)

    # Show the agent the list the submit will be graded against, derived
    # the same way the gate derives it — never from a second copy of the
    # rule that could drift from the first. The row is already committed,
    # so this reads the same set of months the gate will.
    store = await db.get(Store, task.store_id)
    owed: list[str] = []
    if store is not None:
        months = await declarations.declared_months(db, task_id)
        slug = store_slug(store.name, store.id)
        # Pure: reads the store's own JSON columns and the calendar. No
        # filesystem, so nothing to hand to a thread.
        owed = [
            entry.relpath for entry in manifest_for_months(store, slug, months)
        ]

    out = {
        'seq': row.seq,
        'month': row.month,
        'created_at': row.created_at,
        'reporting_month': expected,
        'expected_files': owed,
    }
    if month != expected:
        out['note'] = (
            f'{month} was declared, not the derived reporting month '
            f'{expected}. That is fine for a back-fill the user asked '
            'for; the check covers exactly the month you named.'
        )
    return out
