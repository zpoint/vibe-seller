"""Which sub-tasks may exist — refused on the ACTION, not prose.

The orchestrator prompt tells an agent HOW to delegate; these rules
make the shapes that break the system impossible:

* the named parent must be a real task (``parent_task_id`` is
  agent-supplied);
* a sub-task must carry a ``store_id`` — sub-tasks are the
  store-bound unit of work;
* only a top-level task may spawn — depth is capped at 1, so the
  orchestrator tree is flat and recursion is impossible by
  construction;
* a FANOUT-schedule fire may not parent — its batch already creates
  one task per store per fire, and a child would fan out again.

Each violation answers one clear 400 naming the way out — never a
repeated round.
"""

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schedule import Schedule
from app.models.schedule_constants import PhaseMode
from app.models.task import Task


async def validate_parent_task(
    db: AsyncSession, parent_task_id: str, store_id: str | None
) -> None:
    """Raise 400 when a sub-task with this shape must not exist."""
    parent = await db.get(Task, parent_task_id)
    if parent is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f'parent_task_id {parent_task_id!r} names no task. A '
                'sub-task points at the task it belongs to (your own '
                'task id) and that task must still exist.'
            ),
        )
    if not store_id:
        raise HTTPException(
            status_code=400,
            detail=(
                f'A sub-task of {parent_task_id} must carry a '
                'store_id: sub-tasks are the store-bound unit of '
                'work. Do the no-store part in task '
                f'{parent_task_id} itself.'
            ),
        )
    if parent.parent_task_id:
        raise HTTPException(
            status_code=400,
            detail=(
                f'Task {parent.id} is itself a sub-task of '
                f'{parent.parent_task_id}; a sub-task cannot create '
                'sub-tasks. Do the store work in this task — it '
                'already has its store — and report what you could '
                'not do in your result.'
            ),
        )
    if not parent.schedule_id:
        return
    sched = await db.get(Schedule, parent.schedule_id)
    if sched is None or sched.phase_mode != PhaseMode.FANOUT:
        return
    raise HTTPException(
        status_code=400,
        detail=(
            f'Task {parent.id} is a fire of a FANOUT schedule '
            f'({parent.schedule_id}), and the scheduler already '
            'creates one task per store per fire. A sub-task '
            'spawned here would be created once per store and would '
            'fan out again. Do the store-bound work inside this task '
            f'(store {parent.store_id} is already bound to it); if '
            'separate follow-up work is needed, ask the user to '
            'create it.'
        ),
    )
