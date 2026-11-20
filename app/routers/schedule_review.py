"""Plan review for review schedules: approve, request changes, attention.

A separate router from ``app/routers/schedules.py`` (which is at the
800-line limit) under the same prefix. It is registered BEFORE that
router in ``app/main.py``: ``GET /api/schedules/attention`` would
otherwise be captured by ``GET /api/schedules/{schedule_id}``.

Who may decide: a person in the web UI, or an external agent acting for
them through ``/api/mcp`` with their ``vst_`` token (the token resolves
to its owner; the MCP tools call these handlers in-process). Never an
in-task agent: every credential a task agent holds — the in-task MCP
cookie and the browser wrapper's Bearer service token — resolves to the
``ai_bot`` user, and :func:`_refuse_task_agent` turns that away, so no
task can approve (or rewrite) the plan it is held to.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.config import AI_BOT_ROLE
from app.database import get_db
from app.events.bus import event_bus
from app.models.schedule import Schedule
from app.models.user import User
from app.plan_states import PlanStatus
from app.routers.schedule_planning import (
    abort_current_planning_task,
    finalize_pending_abort,
    schedule_needs_plan,
    spawn_planning_task_or_fail,
)
from app.routers.schedules import _enrich_response
from app.schedule_plan_review import approve_proposal, check_plan_version
from app.schemas.schedule import (
    PlanApprovalItem,
    PlanApproveRequest,
    PlanChangesRequest,
    ScheduleAttentionResponse,
    ScheduleResponse,
)

router = APIRouter(prefix='/api/schedules', tags=['schedules'])


# Where a human finds these decisions in the UI (403 text).
UI_WHERE = 'the schedule\'s "Plan" panel, or the banner at the top'

# A revision may start from a proposal or from the approved plan.
_REVISABLE = {
    PlanStatus.PENDING_APPROVAL.value,
    PlanStatus.READY.value,
}


def _refuse_task_agent(user: User) -> None:
    if user.role == AI_BOT_ROLE:
        raise HTTPException(
            status_code=403,
            detail=(
                'Only the schedule owner decides on a plan (in '
                f'{UI_WHERE}, or through an agent they connected over '
                'MCP). A task agent cannot approve or change it.'
            ),
        )


async def _review_schedule(db: AsyncSession, schedule_id: str) -> Schedule:
    schedule = await db.get(Schedule, schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail='Schedule not found')
    if not schedule_needs_plan(schedule):
        raise HTTPException(
            status_code=400,
            detail=(
                'This schedule runs automatically — it has no plan to '
                'review. Turn on plan review to use one.'
            ),
        )
    return schedule


@router.get('/attention', response_model=ScheduleAttentionResponse)
async def schedule_attention(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """What waits for a human: plans awaiting approval (the top banner)."""
    rows = (
        await db.execute(
            select(Schedule)
            .where(
                Schedule.plan_status == PlanStatus.PENDING_APPROVAL.value,
                Schedule.plan_mode.is_(True),
                Schedule.is_system.is_not(True),
            )
            .order_by(Schedule.updated_at)
        )
    ).scalars()
    return ScheduleAttentionResponse(
        plan_approvals=[
            PlanApprovalItem(
                schedule_id=s.id,
                title=s.title,
                store_id=s.store_id,
                plan_version=s.plan_version,
                proposed_at=s.updated_at,
            )
            for s in rows
        ]
    )


@router.post('/{schedule_id}/plan/approve', response_model=ScheduleResponse)
async def approve_plan(
    schedule_id: str,
    data: PlanApproveRequest | None = None,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Approve the pending plan: from now on every fire runs it.

    409 when nothing is pending; 412 when the plan changed since the
    caller read it (``plan_version``).
    """
    _refuse_task_agent(_user)
    schedule = await _review_schedule(db, schedule_id)
    await approve_proposal(db, schedule, data.plan_version if data else None)
    await event_bus.emit(
        'schedule_plan_ready',
        {'schedule_id': schedule.id, 'plan_version': schedule.plan_version},
    )
    return await _enrich_response(schedule)


@router.post(
    '/{schedule_id}/plan/request-changes', response_model=ScheduleResponse
)
async def request_plan_changes(
    schedule_id: str,
    data: PlanChangesRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Send the plan back with what to change: a new planner revises it.

    Allowed on a pending proposal or on the approved plan. While the
    revision is written and reviewed, an already-approved plan keeps
    firing (``app.plan_states.review_plan_fires``).
    """
    _refuse_task_agent(current_user)
    feedback = (data.feedback or '').strip()
    if not feedback:
        raise HTTPException(
            status_code=400,
            detail='Say what should change in the plan (feedback).',
        )
    schedule = await _review_schedule(db, schedule_id)
    if schedule.plan_status not in _REVISABLE:
        raise HTTPException(
            status_code=409,
            detail=(
                'There is no finished plan to revise right now '
                f'(plan_status={schedule.plan_status!r}).'
            ),
        )
    check_plan_version(schedule, data.plan_version)
    await abort_current_planning_task(
        schedule, db, reason='Superseded by a change request.'
    )
    schedule.plan_feedback = feedback
    schedule.updated_at = datetime.now(UTC).isoformat()
    await spawn_planning_task_or_fail(schedule, current_user, db)
    await finalize_pending_abort(schedule)
    await event_bus.emit(
        'schedule_plan_changed',
        {'schedule_id': schedule.id, 'plan_status': schedule.plan_status},
    )
    await db.refresh(schedule)
    return await _enrich_response(schedule)
