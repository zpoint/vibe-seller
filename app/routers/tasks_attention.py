"""What waits for a person on tasks: the top "waiting for you" bar.

``GET /api/tasks/attention`` lists every task that moves on only when
someone acts, of any kind (store or not, sub-task or not, auto or
plan mode):

* ``question`` — a question waiting for an answer, live in its agent or
  parked on the row;
* ``reply`` — any other wait only a person's message ends (a manual
  wait-condition, steps left undone, a prose question);
* ``plan`` — a plan-mode task's plan waiting for review (the same rule
  the task view shows its Approve button by).

Left out: waits on an outside event (email), a parent waiting on its
sub-tasks (nothing a person does here moves it), a scheduled fire
queued as PLANNED (its plan is already approved) and a review
schedule's planner, which ``/api/schedules/attention`` lists. The SPA
shows ONE bar and answers come first (``AttentionBanner``).

Registered before ``app/routers/tasks.py``: ``GET /api/tasks/{task_id}``
would otherwise capture ``/attention``.
"""

import json

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.question_policy import waits_on_a_person
from app.auth import get_current_user
from app.database import get_db
from app.models.task import Task
from app.models.user import User
from app.question_park import pending_question_for
from app.scheduler.waiting import wait_deadline
from app.schemas.task import TaskAttentionResponse, WaitingTaskItem
from app.task_states import TaskStatus

router = APIRouter(prefix='/api/tasks', tags=['tasks'])

#: A live question is held by a running agent; a parked one or any other
#: wait on a person is on a WAITING row; a plan to review on a PLANNED one.
_CANDIDATES = (
    TaskStatus.WAITING,
    TaskStatus.RUNNING,
    TaskStatus.DESIGNING,
    TaskStatus.PLANNED,
)


def _condition(task: Task) -> dict:
    try:
        cond = json.loads(task.wait_condition or '{}')
    except (json.JSONDecodeError, TypeError):
        return {}
    return cond if isinstance(cond, dict) else {}


def awaits_plan_review(task: Task) -> bool:
    """A plan-mode task whose plan waits for the user's review — the
    rule the task view shows Approve by (``TasksView``): a scheduled
    fire sits in PLANNED only while queued, its plan already approved."""
    return (
        task.status == TaskStatus.PLANNED
        and bool(task.plan_mode)
        and not task.schedule_id
    )


def waiting_on_person(task: Task) -> WaitingTaskItem | None:
    """The banner entry for *task*, or None when no person is needed."""
    cond = _condition(task)
    if awaits_plan_review(task):
        kind = 'plan'
    elif pending_question_for(task.id, task) is not None:
        kind = 'question'
    elif (
        task.status == TaskStatus.WAITING
        and cond.get('strategy') != 'children'
        and waits_on_a_person(cond)
    ):
        kind = 'reply'
    else:
        return None
    deadline = (
        wait_deadline(cond) if task.status == TaskStatus.WAITING else None
    )
    return WaitingTaskItem(
        task_id=task.id,
        title=task.title,
        store_id=task.store_id,
        kind=kind,
        since=cond.get('waiting_since') or task.updated_at,
        deadline=deadline.isoformat() if deadline else None,
    )


@router.get('/attention', response_model=TaskAttentionResponse)
async def task_attention(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Tasks waiting on a person, the one that has waited longest first."""
    rows = (
        await db.execute(
            select(Task).where(
                Task.status.in_(_CANDIDATES),
                Task.is_plan_only.is_not(True),
            )
        )
    ).scalars()
    items = [item for task in rows if (item := waiting_on_person(task))]
    items.sort(key=lambda item: item.since or '')
    return TaskAttentionResponse(waiting_tasks=items)
