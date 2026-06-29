"""Workflow tests: schedule fires inherit the owner's live default profile.

Regression for the frozen-snapshot bug: a schedule's ``ai_profile_id``
was resolved ONCE at creation (``data.ai_profile_id or
current_user.default_profile_id or 'default'``) and then copied
verbatim on every fire, so switching the default provider (e.g. to
deepseek during a Claude outage) silently left every existing schedule
pinned to the old provider — scheduled auto-fires kept hitting
Anthropic (529) while manual tasks, which resolve the default live,
recovered.

The fix: schedules store NULL ("inherit") unless an explicit pin was
chosen, and every fire path resolves the owner's CURRENT
``default_profile_id`` live via :func:`resolve_schedule_profile`.
These tests pin that invariant directly on the resolver and
end-to-end through ``run_single_job``.
"""

import uuid

import pytest
from sqlalchemy import select

from app.ai.profiles import resolve_schedule_profile
from app.models.schedule import Schedule
from app.models.schedule_constants import PhaseMode
from app.models.task import Task
from app.models.user import User
from app.scheduler.fanout import run_single_job

pytestmark = pytest.mark.workflow


async def _make_schedule(db, created_by, ai_profile_id=None):
    """Insert a plan_mode=False single schedule that always fires."""
    sched = Schedule(
        id=str(uuid.uuid4()),
        title='profile-inherit',
        description='d',
        schedule_type='minutes',
        schedule_time='00:00',
        interval_value=5,
        plan_mode=False,
        phase_mode=PhaseMode.SINGLE.value,
        ai_profile_id=ai_profile_id,
        created_by=created_by,
    )
    db.add(sched)
    await db.commit()
    await db.refresh(sched)
    return sched


class TestResolveScheduleProfile:
    """Pin the resolver contract — the core of the fix."""

    async def test_null_inherits_owner_current_default(
        self, override_async_session, admin_user
    ):
        async with override_async_session() as db:
            owner = await db.get(User, admin_user.id)
            owner.default_profile_id = 'deepseek'
            sched = await _make_schedule(db, admin_user.id, None)
            assert await resolve_schedule_profile(sched, db) == 'deepseek'

    async def test_default_literal_inherits(
        self, override_async_session, admin_user
    ):
        # 'default' is the Schedule.ai_profile_id column default (a
        # freshly-created unpinned schedule holds 'default', never
        # NULL) and the legacy snapshot value — it MUST be treated as
        # "inherit", not as a Claude pin, or the failover stays broken.
        async with override_async_session() as db:
            owner = await db.get(User, admin_user.id)
            owner.default_profile_id = 'deepseek'
            sched = await _make_schedule(db, admin_user.id, 'default')
            assert await resolve_schedule_profile(sched, db) == 'deepseek'

    async def test_switching_default_is_live_failover(
        self, override_async_session, admin_user
    ):
        async with override_async_session() as db:
            owner = await db.get(User, admin_user.id)
            owner.default_profile_id = 'deepseek'
            sched = await _make_schedule(db, admin_user.id, None)
            assert await resolve_schedule_profile(sched, db) == 'deepseek'
            # Flip the owner default AFTER the schedule exists. The
            # same 'default'/inherit schedule row must pick up the new
            # default with no re-freeze — this is the failover that
            # was broken.
            owner.default_profile_id = 'glm'
            await db.commit()
            assert await resolve_schedule_profile(sched, db) == 'glm'

    async def test_explicit_pin_wins_over_owner_default(
        self, override_async_session, admin_user
    ):
        async with override_async_session() as db:
            owner = await db.get(User, admin_user.id)
            owner.default_profile_id = 'deepseek'
            sched = await _make_schedule(db, admin_user.id, 'kimi')
            assert await resolve_schedule_profile(sched, db) == 'kimi'

    async def test_missing_owner_falls_back_to_default(
        self, override_async_session, admin_user
    ):
        async with override_async_session() as db:
            sched = await _make_schedule(db, 'no-such-user', None)
            assert await resolve_schedule_profile(sched, db) == 'default'

    async def test_none_schedule_returns_default(self, override_async_session):
        async with override_async_session() as db:
            assert await resolve_schedule_profile(None, db) == 'default'


class TestSingleJobInheritsProfile:
    """End-to-end: run_single_job assigns the resolved profile to the task."""

    async def test_fired_task_uses_owner_default_when_schedule_null(
        self, override_async_session, admin_user, install_fake_agent
    ):
        async with override_async_session() as db:
            owner = await db.get(User, admin_user.id)
            owner.default_profile_id = 'deepseek'
            sched = await _make_schedule(db, admin_user.id, None)
            sched_id = sched.id

        # run_single_job hardcodes created_by=DEFAULT_USER_ID on the
        # task, but resolves the profile from the SCHEDULE owner
        # (admin_user) — so the task must still land on deepseek.
        await run_single_job(schedule_id=sched_id, task_title='t')

        async with override_async_session() as db:
            result = await db.execute(
                select(Task).where(Task.schedule_id == sched_id)
            )
            tasks = result.scalars().all()
        assert len(tasks) == 1
        assert tasks[0].ai_profile_id == 'deepseek'
