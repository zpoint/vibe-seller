"""Shared helpers for workflow tests of review schedules.

A review schedule (``plan_mode=True``) goes planning → pending_approval
on its own (the FakeAgent's ExitPlanMode hands the plan over), and only
an explicit approval makes it ``ready``. These helpers drive that last
step the way the web UI does: read the pending version, approve it.
"""

import asyncio

from sqlalchemy import select

import app.database as _db
from app.models.schedule import Schedule


async def wait_plan_status(schedule_id: str, target: str, timeout=5.0):
    """Poll the DB directly (API polling races the FakeAgent's commit
    under StaticPool) until the schedule reaches *target*."""
    for _ in range(int(timeout / 0.05)):
        async with _db.async_session() as db:
            s = (
                await db.execute(
                    select(Schedule).where(Schedule.id == schedule_id)
                )
            ).scalar_one_or_none()
            if s is not None and s.plan_status == target:
                return {
                    'plan_status': s.plan_status,
                    'plan_version': s.plan_version,
                    'plan': s.plan,
                    'proposed_plan': s.proposed_plan,
                    'current_planning_task_id': s.current_planning_task_id,
                }
        await asyncio.sleep(0.05)
    raise AssertionError(
        f'Schedule {schedule_id} plan_status did not reach {target}'
    )


async def approve_when_proposed(client, schedule_id: str) -> dict:
    """Wait for the planner's proposal, then approve it (as the UI does).

    Returns the schedule as the approve route answers it (``ready``).
    """
    pending = await wait_plan_status(schedule_id, 'pending_approval')
    r = await client.post(
        f'/api/schedules/{schedule_id}/plan/approve',
        json={'plan_version': pending['plan_version']},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['plan_status'] == 'ready'
    return body
