"""Which tasks have a live launch in this process right now.

A task row saying QUEUED (or a scheduled PENDING/PLANNED) is a promise
that something in this process will start it. The promise is kept by
one of three holders, and only by them:

- a lane in ``TaskQueueScheduler`` (waiting for its store's turn, or
  dispatched and not yet finished);
- a launcher coroutine (``auto_run_task`` / ``execute_planned_task`` /
  ``execute_woken_task``) preparing the run, including a direct launch
  that never went through the queue;
- ``agent_manager.run`` waiting on the agent-slot limiter.

When none of them holds the task the row is an orphan: nothing will
ever start it, and before the orphan reconciler only a server restart
did (``_recover_from_db`` re-queues every QUEUED row). This module is
the in-memory record of the last two holders, so the reconciler can
tell an orphan from a task that is merely waiting its turn.
"""

import collections
from collections.abc import Awaitable, Callable
import contextlib
import functools
from typing import ParamSpec, TypeVar

_P = ParamSpec('_P')
_R = TypeVar('_R')

# task_id -> number of live holders (a retry can overlap a launch that
# is still unwinding, so this is a count, not a set).
_holders: collections.Counter[str] = collections.Counter()


@contextlib.contextmanager
def holding(task_id: str):
    """Mark ``task_id`` as held for the duration of the block."""
    _holders[task_id] += 1
    try:
        yield
    finally:
        _holders[task_id] -= 1
        if _holders[task_id] <= 0:
            del _holders[task_id]


def holds_task(
    fn: Callable[_P, Awaitable[_R]],
) -> Callable[_P, Awaitable[_R]]:
    """Decorate a launcher whose first argument is the task id."""

    @functools.wraps(fn)
    async def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        task_id = args[0] if args else kwargs['task_id']
        with holding(task_id):
            return await fn(*args, **kwargs)

    return wrapper


def is_held(task_id: str) -> bool:
    """True while a launcher or a slot wait holds ``task_id``."""
    return _holders.get(task_id, 0) > 0
