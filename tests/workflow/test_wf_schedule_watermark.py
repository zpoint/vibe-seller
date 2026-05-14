"""Workflow tests for schedule last_completed_at watermark injection.

Verifies that build_system_context injects the correct previous-run
timestamp (or first-run message) into the agent prompt for scheduled
tasks.
"""

from datetime import UTC, datetime
import uuid

import pytest
import pytest_asyncio

from app.models.schedule import Schedule
from app.models.task import Task
from app.task_states import TaskStatus
from tests.workflow.conftest import wait_for_task

pytestmark = pytest.mark.workflow


@pytest_asyncio.fixture
async def schedule(override_async_session, admin_user):
    """Create a Schedule row owned by admin_user."""
    async with override_async_session() as db:
        sched = Schedule(
            id=str(uuid.uuid4()),
            title='Daily email check',
            schedule_type='days',
            schedule_time='09:00',
            interval_value=1,
            created_by=admin_user.id,
        )
        db.add(sched)
        await db.commit()
        await db.refresh(sched)
        return sched


@pytest.fixture
def schedule_payload(schedule):
    """Return partial JSON payload with schedule_id for task creation."""
    return {'schedule_id': schedule.id}


# ── Test 1: watermark injected ────────────────────────


class TestScheduleWatermark:
    async def test_previous_completed_timestamp_injected(
        self,
        admin_client,
        install_fake_agent,
        override_async_session,
        schedule,
        schedule_payload,
    ):
        """Second run sees 'Previous run completed at:' with
        the first run's completed_at timestamp.
        """
        ts = datetime(2026, 3, 17, 9, 0, 0, tzinfo=UTC).isoformat()

        # Insert a completed sibling task
        async with override_async_session() as db:
            t1 = Task(
                id=str(uuid.uuid4()),
                title='Run 1',
                schedule_id=schedule.id,
                created_by=schedule.created_by,
                status=TaskStatus.COMPLETED,
                completed_at=ts,
                created_at=ts,
                updated_at=ts,
            )
            db.add(t1)
            await db.commit()

        # Create a new task with schedule_id via API
        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Run 2', **schedule_payload},
        )
        assert r.status_code == 200
        task_id = r.json()['id']

        await wait_for_task(admin_client, task_id)

        # Check the system_extra captured by FakeAgent
        calls = install_fake_agent.get_calls(task_id=task_id, action='run')
        assert len(calls) == 1
        assert f'Previous run completed at: {ts}' in calls[0].system_extra

    # ── Test 2: first run ─────────────────────────────

    async def test_first_run_message(
        self,
        admin_client,
        install_fake_agent,
        schedule,
        schedule_payload,
    ):
        """First run (no completed sibling) sees 'This is the
        first run' message.
        """
        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'First run', **schedule_payload},
        )
        assert r.status_code == 200
        task_id = r.json()['id']

        await wait_for_task(admin_client, task_id)

        calls = install_fake_agent.get_calls(task_id=task_id, action='run')
        assert len(calls) == 1
        assert 'This is the first run of this schedule' in calls[0].system_extra

    # ── Test 3: failed sibling skipped ────────────────

    async def test_failed_sibling_skipped(
        self,
        admin_client,
        install_fake_agent,
        override_async_session,
        schedule,
        schedule_payload,
    ):
        """T3 sees T1's completed_at, not T2's (which failed)."""
        ts1 = datetime(2026, 3, 16, 9, 0, 0, tzinfo=UTC).isoformat()
        ts2 = datetime(2026, 3, 17, 9, 0, 0, tzinfo=UTC).isoformat()

        async with override_async_session() as db:
            # T1: completed
            db.add(
                Task(
                    id=str(uuid.uuid4()),
                    title='T1 completed',
                    schedule_id=schedule.id,
                    created_by=schedule.created_by,
                    status=TaskStatus.COMPLETED,
                    completed_at=ts1,
                    created_at=ts1,
                    updated_at=ts1,
                )
            )
            # T2: failed (has a timestamp, but should be skipped)
            db.add(
                Task(
                    id=str(uuid.uuid4()),
                    title='T2 failed',
                    schedule_id=schedule.id,
                    created_by=schedule.created_by,
                    status=TaskStatus.FAILED,
                    completed_at=ts2,
                    created_at=ts2,
                    updated_at=ts2,
                )
            )
            await db.commit()

        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'T3 new run', **schedule_payload},
        )
        assert r.status_code == 200
        task_id = r.json()['id']

        await wait_for_task(admin_client, task_id)

        calls = install_fake_agent.get_calls(task_id=task_id, action='run')
        assert len(calls) == 1
        # Should see T1's timestamp, not T2's
        assert f'Previous run completed at: {ts1}' in calls[0].system_extra
        assert ts2 not in calls[0].system_extra
