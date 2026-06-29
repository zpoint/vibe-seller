"""Periodic reaper that fires a fanout batch's parent "finalize" step.

This is the join half of the general fan-out → join orchestration
shape (the mirror of ``two_phase``, which runs a prerequisite BEFORE
the children). It is **pure plumbing** — it makes no decision about
what to do after the children succeed or fail. It guarantees exactly:

  (a) the finalize agent task runs exactly ONCE, after every per-store
      child of the batch is terminal (COMPLETED or FAILED),
  (b) it is handed the children's results + workspace locations via a
      ``batch_results.json`` written into its task dir,
  (c) it is an ordinary no-store agent task, so it already has the
      normal toolset (read/write workspace, create tasks, message…).

Everything past that — retry which failures, summarize, publish one
PR, notify — is decided by the AI agent following the schedule's
``finalize_description`` prompt. There is no retry/aggregation/policy
logic here on purpose: this is an AI platform; the orchestration shape
is the framework's job, the policy is the prompt's.

Why a periodic reaper rather than a callback on the last child's
terminal write: terminal status is written from ~5 different
coroutines (auto_run_task, the stop hook, stall_reaper, plan_reaper,
fanout's WAITING-cancel). A single periodic single-writer (mirrors
``plan_reaper`` / ``stall_reaper``) is the robust place to observe
"all children terminal" without racing any of them. ``max_instances=1``
+ ``coalesce=True`` on the APScheduler job means only one reaper runs
at a time, so the fire-once guarantee needs no cross-process lock.

Why an explicit ``Task.is_finalize`` flag rather than "store_id IS
NULL": a ``two_phase`` schedule's L2 prerequisite task is ALSO
``store_id=None`` + ``batch_id``-bearing. Keying off NULL would
mistake it for the finalize task. The flag makes the contract
explicit in the schema instead of in a fragile convention.
"""

import json
import logging
import uuid

from sqlalchemy import select

from app.ai.profiles import resolve_schedule_profile
from app.browser.manager import store_slug
from app.database import async_session
from app.events.bus import event_bus
from app.models.schedule import Schedule
from app.models.store import Store
from app.models.task import Task
from app.scheduler.task_queue import task_queue_scheduler
from app.task_states import TaskStatus
from app.workspace.manager import workspace_manager

logger = logging.getLogger(__name__)

# A child counts toward "the batch is done" once it reaches any of
# these. FAILED children still let finalize run — the parent prompt
# decides whether to retry or just report them.
_TERMINAL = {TaskStatus.COMPLETED, TaskStatus.FAILED}

_BATCH_RESULTS_FILE = 'batch_results.json'

# Appended to the schedule's finalize_description so the agent knows
# where the batch result set lives (it runs with its task dir as cwd).
_RESULTS_POINTER = (
    '\n\n---\n'
    f'The results of every store in this batch are in `./{_BATCH_RESULTS_FILE}`'
    " (a JSON object: `children` is a list with each store's `status`"
    ' (completed|failed), `result`, `error`, `store_slug`, and `task_dir`'
    " — the store's own working directory, readable for its outputs)."
    ' Decide what to do based on those results.'
)


def _child_record(task: Task, slug: str, task_dir: str) -> dict:
    """One child's entry in batch_results.json (pure, unit-tested)."""
    return {
        'task_id': task.id,
        'store_id': task.store_id,
        'store_slug': slug,
        'status': task.status,
        'result': task.result,
        'error': task.error,
        'task_dir': task_dir,
        'started_at': task.started_at,
        'completed_at': task.completed_at,
    }


async def reap_finalized_batches() -> None:
    """Fire the finalize task for any batch whose children are all done.

    Idempotent: a batch that already has an ``is_finalize`` task is
    skipped, so re-running every tick is a no-op. A batch a parent
    later re-opens by spawning retry children (same ``batch_id``)
    won't re-fire — the finalize task still exists.
    """
    async with async_session() as db:
        scheds = (
            (
                await db.execute(
                    select(Schedule).where(
                        Schedule.finalize_description.isnot(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        sched_by_id = {
            s.id: s for s in scheds if (s.finalize_description or '').strip()
        }
        if not sched_by_id:
            return

        # Only load tasks for batches that have NOT finalized yet — a
        # batch with an is_finalize task can never change readiness, so
        # excluding them keeps this per-minute scan bounded to in-flight
        # batches instead of growing with all historical batches.
        finalized_batches = select(Task.batch_id).where(
            Task.is_finalize.is_(True), Task.batch_id.isnot(None)
        )
        rows = (
            (
                await db.execute(
                    select(Task).where(
                        Task.schedule_id.in_(list(sched_by_id)),
                        Task.batch_id.isnot(None),
                        Task.batch_id.notin_(finalized_batches),
                    )
                )
            )
            .scalars()
            .all()
        )

    # Group by batch; decide which batches are ready to finalize.
    batches: dict[str, list[Task]] = {}
    for t in rows:
        batches.setdefault(t.batch_id, []).append(t)

    for batch_id, tasks in batches.items():
        if any(t.is_finalize for t in tasks):
            continue  # already finalized (or finalize in flight)
        children = [t for t in tasks if t.store_id is not None]
        if not children:
            continue  # no per-store children yet — nothing to reduce
        if not all(t.status in _TERMINAL for t in children):
            continue  # still running
        sched = sched_by_id.get(children[0].schedule_id)
        if sched is None:
            continue
        await _fire_finalize(batch_id, sched)


async def _fire_finalize(batch_id: str, sched: Schedule) -> None:
    """Create + dispatch the one finalize task for *batch_id*.

    Writes ``batch_results.json`` into the task dir BEFORE submitting
    so the agent never starts before the file exists (tasks run only
    via the queue's ``submit``; there is no background PENDING scan).
    Re-checks BOTH the is_finalize guard and the all-children-terminal
    condition inside the write transaction — belt-and-braces against a
    second writer and against a child being added/reset in the window
    between the outer scan and here.
    """
    finalize_id = str(uuid.uuid4())
    async with async_session() as db:
        existing = (
            await db.execute(
                select(Task.id).where(
                    Task.batch_id == batch_id,
                    Task.is_finalize.is_(True),
                )
            )
        ).first()
        if existing:
            return  # someone beat us to it

        children = (
            (
                await db.execute(
                    select(Task).where(
                        Task.batch_id == batch_id,
                        Task.store_id.isnot(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        if not children:
            return
        # Re-verify all children are still terminal — preserves the
        # "fire only after every child is terminal" guarantee even if
        # one was added/reset since the outer scan.
        if not all(c.status in _TERMINAL for c in children):
            return

        # Resolve slugs from the children's stores.
        store_ids = [c.store_id for c in children]
        stores = (
            (await db.execute(select(Store).where(Store.id.in_(store_ids))))
            .scalars()
            .all()
        )
        store_by_id = {s.id: s for s in stores}

        task_root = workspace_manager.root / 'tasks'
        records = []
        for c in children:
            store = store_by_id.get(c.store_id)
            slug = (
                store_slug(store.name, store.id)
                if store
                else (c.store_id or '')
            )
            child_dir = str(task_root / c.id)
            records.append(_child_record(c, slug, child_dir))

        completed = sum(
            1 for r in records if r['status'] == TaskStatus.COMPLETED
        )
        failed = len(records) - completed

        finalize = Task(
            id=finalize_id,
            store_id=None,
            schedule_id=sched.id,
            batch_id=batch_id,
            is_finalize=True,
            created_by=sched.created_by,
            title=f'{sched.title} — finalize',
            description=(sched.finalize_description or '') + _RESULTS_POINTER,
            status=TaskStatus.PENDING,
            plan_mode=False,
            skip_reflection=True,
            ai_profile_id=await resolve_schedule_profile(sched, db),
        )
        db.add(finalize)
        await db.commit()

    payload = {
        'schema': 'batch-results/v1',
        'batch_id': batch_id,
        'schedule_id': sched.id,
        'completed': completed,
        'failed': failed,
        'children': records,
    }

    # Create the task dir (+ workspace symlinks) and drop the results
    # in BEFORE the agent could run.
    task_dir = await workspace_manager.prepare_task_workspace(
        finalize_id, store_id=None
    )
    (task_dir / _BATCH_RESULTS_FILE).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8'
    )

    logger.info(
        'Finalize fired for batch %s (schedule=%s): %d completed, %d failed',
        batch_id,
        sched.id,
        completed,
        failed,
    )

    await event_bus.emit(
        'fanout_finalize_triggered',
        {
            'schedule_id': sched.id,
            'batch_id': batch_id,
            'task_id': finalize_id,
            'completed': completed,
            'failed': failed,
        },
    )

    try:
        await task_queue_scheduler.submit(finalize_id, None)
    except Exception:
        logger.exception(
            'Finalize: failed to submit task %s (batch=%s)',
            finalize_id,
            batch_id,
        )
