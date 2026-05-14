"""Shared task deletion logic.

Used by:
- ``DELETE /api/tasks/{id}`` (user-initiated, see app/routers/tasks.py)
- daily auto-cleanup job (see app/scheduler/task_cleanup.py)

Deletes child rows that have FKs to ``tasks.id`` (no cascade in
SQLite), unlinks any sub-tasks, removes the task workspace dir on
disk, and finally drops the row.
"""

import asyncio
import logging
from pathlib import Path
import shutil

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.claude_backend_manager import agent_manager
from app.models.screenshot import Screenshot
from app.models.task import Task
from app.models.task_attachment import TaskAttachment
from app.models.task_log import TaskLog
from app.models.task_message import TaskMessage
from app.models.task_step import TaskStep
from app.task_states import ACTIVE
from app.workspace.manager import VIBE_SELLER_DIR

logger = logging.getLogger(__name__)


def _task_workspace_dir(task_id: str) -> Path:
    """Resolve a task's on-disk workspace path safely."""
    tasks_root = (VIBE_SELLER_DIR / 'tasks').resolve()
    candidate = (tasks_root / task_id).resolve()
    if not candidate.is_relative_to(tasks_root):
        raise ValueError(
            f'Refusing to resolve task dir outside root: {task_id}'
        )
    return candidate


async def _collect_descendants(
    db: AsyncSession, root_task_id: str
) -> list[str]:
    """Return ``root`` followed by every descendant task id (BFS).

    Order matters: callers process children before parents so the
    final ``DELETE`` on the root happens after all FK references
    are gone.
    """
    order: list[str] = [root_task_id]
    frontier: list[str] = [root_task_id]
    seen = {root_task_id}
    while frontier:
        result = await db.execute(
            select(Task.id).where(Task.parent_task_id.in_(frontier))
        )
        next_ids = [row[0] for row in result.all() if row[0] not in seen]
        if not next_ids:
            break
        order.extend(next_ids)
        seen.update(next_ids)
        frontier = next_ids
    return order


async def delete_task(
    db: AsyncSession,
    task_id: str,
    *,
    allow_active: bool = False,
) -> bool:
    """Cascade-delete a task and its full subtree.

    Children are deleted before parents so FK constraints stay
    satisfied. Returns True if at least one row was deleted, False
    if the root task was not found. Raises ValueError if any task
    in the subtree is ACTIVE and ``allow_active`` is False — the
    HTTP endpoint translates that into 409 so the user stops the
    in-flight task first.
    """
    root = await db.get(Task, task_id)
    if not root:
        return False

    ids = await _collect_descendants(db, task_id)

    if not allow_active:
        active_states = {s.value for s in ACTIVE}
        active_rows = (
            await db.execute(
                select(Task.id, Task.status).where(
                    Task.id.in_(ids),
                    Task.status.in_(active_states),
                )
            )
        ).all()
        if active_rows:
            statuses = ', '.join(sorted({r[1] for r in active_rows}))
            raise ValueError(
                f'Cannot delete task tree: {len(active_rows)} task(s) '
                f'in status {{{statuses}}} — stop them first'
            )

    # Best-effort: stop any running agent for every task in the
    # subtree before we touch their rows.
    for tid in ids:
        try:
            await agent_manager.stop(tid)
        except Exception:
            logger.debug('agent_manager.stop failed for %s', tid, exc_info=True)

    # Bulk-delete dependent rows: one query per child table covers
    # the whole subtree (5 queries total instead of 5 × len(subtree)).
    for model in (
        TaskStep,
        TaskMessage,
        TaskAttachment,
        TaskLog,
        Screenshot,
    ):
        await db.execute(delete(model).where(model.task_id.in_(ids)))

    # Delete Task rows leaves → root so each parent's children are
    # gone before its own row drops (matters when SQLite is run with
    # PRAGMA foreign_keys=ON). Delete is a synchronous user action,
    # so we don't loop to catch hypothetical concurrent inserts under
    # the subtree — if one slipped in, the FK constraint fails loudly
    # rather than silently orphaning.
    for tid in reversed(ids):
        await db.execute(delete(Task).where(Task.id == tid))
    await db.commit()

    # Remove workspace dirs off the event loop.
    for tid in ids:
        try:
            task_dir = _task_workspace_dir(tid)
        except ValueError:
            continue
        if task_dir.exists():
            await asyncio.to_thread(shutil.rmtree, task_dir, ignore_errors=True)
    return True


async def list_expired_task_ids(
    db: AsyncSession,
    *,
    older_than_iso: str,
) -> list[str]:
    """Return ids of cleanup-eligible tasks.

    Eligibility (auto-cleanup is conservative — manual delete is
    not constrained by these rules):
    - status COMPLETED or FAILED
    - updated_at older than ``older_than_iso``
    - NOT ``is_plan_only`` (these are the frozen plan authors for
      Schedules; deleting one strands the schedule's plan history)
    - NOT a parent (anything else points at it via parent_task_id).
      Fanout / scheduled parents stay around so the user can still
      see the run summary; only leaves get reaped.
    """
    parent_ids_subq = (
        select(Task.parent_task_id)
        .where(Task.parent_task_id.is_not(None))
        .distinct()
        .subquery()
    )
    result = await db.execute(
        select(Task.id).where(
            Task.status.in_(('completed', 'failed')),
            Task.updated_at < older_than_iso,
            Task.is_plan_only.is_(False),
            ~Task.id.in_(select(parent_ids_subq.c.parent_task_id)),
        )
    )
    return [row[0] for row in result.all()]
