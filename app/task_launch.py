"""The single owner of "how does a task start running".

Extracted from ``app.routers.tasks`` because two other routers already
imported it from there — a router reaching into another router for the
rule is the shape that lets the rule drift. It has drifted once:
``/start`` re-derived a narrower, store-only version of this routing and
so refused every no-store task, which stranded any task created with an
attachment (create defers the launch, /start was the only way to resume
it). Keep the rule here, and let callers ask rather than re-decide.
"""

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING

from app.scheduler.task_queue import task_queue_scheduler
from app.task_runner_auto import auto_run_task

if TYPE_CHECKING:
    from app.models.store import Store


async def schedule_or_run(
    task_id: str,
    store: 'Store | None',
    launcher: Callable | None = None,
) -> bool:
    """Route store tasks through the queue scheduler.

    No-store tasks launch immediately (no browser conflict
    possible).  Store tasks go through the queue so the
    same-platform/different-country QUEUE rule is enforced.

    Falls back to direct launch if the scheduler hasn't
    started (e.g. in tests without full app lifespan).

    *launcher* defaults to ``auto_run_task``; pass
    ``execute_planned_task`` for tasks that already have
    a plan.  Note: when routing through the queue the
    launcher is ignored — _dispatch() picks the right
    handler from task state.

    Returns True when the task went to the queue (so it is now QUEUED),
    False when it was launched directly (status unchanged for now — the
    launcher moves it).
    """
    fn = launcher or auto_run_task
    if store and task_queue_scheduler.is_running:
        await task_queue_scheduler.submit(task_id, store.id)
        return True
    asyncio.create_task(fn(task_id, store))
    return False
