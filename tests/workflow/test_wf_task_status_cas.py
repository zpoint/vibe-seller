"""Compare-and-set status writes: the second writer sees the first's.

The lost update this pins: a schedule delete fails its planner while
the launcher's slot callback moves the same row ``pending`` →
``designing``. With read-check-write both read ``pending`` and the
later commit wins — the deleted schedule's planner kept designing.
``transition_if`` makes check and write one statement, so the result
no longer depends on which commit lands last. Both orders are pinned
here, deterministically; ``test_wf_review_fix_b``'s
``TestDeleteScheduleStopsThePlanner`` pins it end to end.
"""

import uuid

import pytest

from app.models.task import Task
from app.task_states import TaskStatus
from app.task_status_cas import FAILABLE, transition_if

pytestmark = pytest.mark.workflow

LAUNCHABLE = (TaskStatus.PENDING, TaskStatus.QUEUED)


@pytest.fixture
def maker(override_async_session):
    """The test database's session factory."""
    return override_async_session


@pytest.fixture
def new_task(maker, admin_user):
    """Insert a planner-shaped task row in *status*; return its id."""

    async def _create(status=TaskStatus.PENDING) -> str:
        task_id = str(uuid.uuid4())
        async with maker() as db:
            db.add(
                Task(
                    id=task_id,
                    title='t',
                    status=status,
                    plan_mode=True,
                    created_by=admin_user.id,
                )
            )
            await db.commit()
        return task_id

    return _create


async def _status(maker, task_id) -> str:
    async with maker() as db:
        return (await db.get(Task, task_id)).status


async def test_abort_first_then_launch_does_not_resurrect(maker, new_task):
    task_id = await new_task()
    async with maker() as db:
        assert await transition_if(
            db, task_id, FAILABLE, TaskStatus.FAILED, error='Schedule deleted.'
        )
    async with maker() as db:
        # The launcher read `pending` before the abort landed; its
        # write must not apply now.
        assert not await transition_if(
            db, task_id, LAUNCHABLE, TaskStatus.DESIGNING
        )
    assert await _status(maker, task_id) == TaskStatus.FAILED


async def test_launch_first_then_abort_still_fails_it(maker, new_task):
    task_id = await new_task()
    async with maker() as db:
        assert await transition_if(
            db, task_id, LAUNCHABLE, TaskStatus.DESIGNING
        )
    async with maker() as db:
        assert await transition_if(db, task_id, FAILABLE, TaskStatus.FAILED)
    async with maker() as db:
        row = await db.get(Task, task_id)
    assert row.status == TaskStatus.FAILED


async def test_extra_columns_land_in_the_same_write(maker, new_task):
    task_id = await new_task()
    async with maker() as db:
        assert await transition_if(
            db,
            task_id,
            FAILABLE,
            TaskStatus.FAILED,
            error='Schedule deleted.',
            error_category='superseded_by_edit',
        )
    async with maker() as db:
        row = await db.get(Task, task_id)
    assert (row.error, row.error_category) == (
        'Schedule deleted.',
        'superseded_by_edit',
    )
    assert row.updated_at


async def test_an_illegal_transition_is_refused_before_writing(maker, new_task):
    task_id = await new_task(TaskStatus.COMPLETED)
    async with maker() as db:
        with pytest.raises(ValueError, match='Invalid task transition'):
            await transition_if(
                db, task_id, (TaskStatus.COMPLETED,), TaskStatus.FAILED
            )
    assert await _status(maker, task_id) == TaskStatus.COMPLETED


def test_failable_is_every_status_with_a_failed_edge():
    assert TaskStatus.COMPLETED not in FAILABLE
    assert TaskStatus.FAILED not in FAILABLE
    assert {TaskStatus.PENDING, TaskStatus.DESIGNING} <= FAILABLE
