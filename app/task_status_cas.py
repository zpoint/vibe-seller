"""Compare-and-set task status writes.

Two coroutines that each READ a task's status, check the transition
in Python and WRITE the new one lose an update when they interleave:
both read ``pending``, both commit, and the later write wins whatever
it says. Observed: deleting a schedule failed its in-flight planner
(``pending`` → ``failed``) while the launcher's slot callback moved
the same row ``pending`` → ``designing``; ``designing`` landed last
and the deleted schedule's planner kept planning, holding an agent
slot (tests/workflow/test_wf_review_fix_b.py,
``TestDeleteScheduleStopsThePlanner``).

:func:`transition_if` makes the check and the write ONE statement —
``UPDATE … WHERE id = :id AND status IN (:from)`` — so whichever
writer lands second sees the status the first one wrote and either
applies its own legal transition from it or reports that it did not.
"""

from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task
from app.task_states import TaskStatus, assert_transition, can_transition


async def transition_if(
    db: AsyncSession,
    task_id: str,
    from_statuses: Iterable[str],
    to_status: str,
    **values,
) -> bool:
    """Move *task_id* to *to_status* only while its status is one of
    *from_statuses*; commit, and say whether the row moved.

    Every ``from → to`` pair must be a legal transition — asked for an
    illegal one, this raises instead of writing it. *values* are
    further columns set in the same statement; ``updated_at`` is
    stamped unless given.
    """
    sources = [TaskStatus(s) for s in from_statuses]
    for source in sources:
        assert_transition(source, to_status)
    values.setdefault('updated_at', datetime.now(UTC).isoformat())
    result = await db.execute(
        update(Task)
        .where(Task.id == task_id, Task.status.in_(sources))
        .values(status=to_status, **values)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return result.rowcount == 1


#: Every status a task can be FAILED from — what an abort may stop.
FAILABLE = frozenset(
    s for s in TaskStatus if can_transition(s, TaskStatus.FAILED)
)
