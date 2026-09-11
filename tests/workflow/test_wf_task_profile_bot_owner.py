"""Workflow tests: a bot-created task inherits the human admin's profile.

The ``'default'`` profile's ``env`` is ``{}``. That is not "the Claude
profile", it is *no provider configured* — an agent launched under it
runs on whatever credentials the host machine happens to carry. The
bot account is login-less, its ``default_profile_id`` is permanently
that placeholder, and no Settings page can change it.

``resolve_schedule_profile`` already fell through a bot owner to the
human admin. Two other paths did not:

* ``create_task`` resolved ``current_user.default_profile_id or
  'default'`` — so every task an integration created as the bot
  landed on the empty-env profile.
* ``spawn_planning_task`` stored ``schedule.ai_profile_id or
  'default'`` — and since ``'default'`` is the *inherit sentinel* as
  well as the column default, an unpinned schedule authored its plan
  on the empty-env profile even when the fire-time resolver would
  later pick the owner's.

These tests drive the real endpoints, not a copy of their
expressions: a regression that drops the resolver from either call
site must fail here.
"""

import uuid

from httpx import ASGITransport, AsyncClient
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.ai.profiles import (
    DEFAULT_PROFILE_ID,
    resolve_owner_profile,
    resolve_schedule_profile,
)
from app.auth import create_token
from app.config import AI_BOT_ROLE, AI_BOT_USER_ID, DEFAULT_USER_ID
from app.main import app
from app.models.schedule import Schedule
from app.models.schedule_constants import PhaseMode
from app.models.task import Task
from app.models.user import User
from app.routers.schedule_planning import spawn_planning_task

pytestmark = pytest.mark.workflow

THIRD_PARTY = 'some-third-party-profile'


async def _seed(db, admin_profile=THIRD_PARTY):
    """The two accounts this rule is about: the bot and the human admin."""
    bot = await db.get(User, AI_BOT_USER_ID)
    if bot is None:
        bot = User(
            id=AI_BOT_USER_ID,
            username='ai_bot',
            email='ai@vibe-seller.local',
            password_hash='disabled',
            role=AI_BOT_ROLE,
        )
        db.add(bot)
    bot.role = AI_BOT_ROLE
    bot.default_profile_id = DEFAULT_PROFILE_ID

    admin = await db.get(User, DEFAULT_USER_ID)
    if admin is None:
        admin = User(
            id=DEFAULT_USER_ID,
            username='human-admin',
            email='admin@example.com',
            password_hash='x',
            role='admin',
        )
        db.add(admin)
    admin.role = 'admin'
    admin.default_profile_id = admin_profile

    await db.commit()
    return bot, admin


@pytest_asyncio.fixture
async def bot_client(
    override_async_session,
    install_fake_agent,
    mock_browser_wf,
    mock_knowledge_sync,
    mock_skills_sync,
    mock_workspace,
    isolated_profiles,
    fast_polling,
):
    """A client authenticated as the login-less bot account."""
    async with override_async_session() as db:
        await _seed(db)
    token = create_token(AI_BOT_USER_ID, AI_BOT_ROLE)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url='http://test'
    ) as client:
        client.cookies.set('auth_token', token)
        yield client


async def _created_task(db, title):
    rows = await db.execute(select(Task).where(Task.title == title))
    return rows.scalars().first()


class TestBotCreatedTaskUsesAdminProfile:
    """POST /api/tasks as the bot — the path that was broken."""

    async def test_inherits_admin_profile(
        self, bot_client, override_async_session
    ):
        r = await bot_client.post(
            '/api/tasks', json={'title': 'bot task', 'defer_start': True}
        )
        assert r.status_code in (200, 201), r.text
        async with override_async_session() as db:
            task = await _created_task(db, 'bot task')
        assert task is not None
        assert task.ai_profile_id == THIRD_PARTY, (
            'a bot-created task must not land on the empty-env '
            f"'{DEFAULT_PROFILE_ID}' profile while an admin default exists"
        )

    async def test_explicit_pin_still_wins(
        self, bot_client, override_async_session
    ):
        r = await bot_client.post(
            '/api/tasks',
            json={
                'title': 'pinned task',
                'defer_start': True,
                'profile_id': 'pinned-profile',
            },
        )
        assert r.status_code in (200, 201), r.text
        async with override_async_session() as db:
            task = await _created_task(db, 'pinned task')
        assert task.ai_profile_id == 'pinned-profile'

    async def test_default_when_admin_expressed_nothing(
        self, bot_client, override_async_session
    ):
        # Nobody configured a provider — 'default' is then the honest
        # answer, not a regression.
        async with override_async_session() as db:
            await _seed(db, admin_profile=DEFAULT_PROFILE_ID)
        r = await bot_client.post(
            '/api/tasks', json={'title': 'no pref', 'defer_start': True}
        )
        assert r.status_code in (200, 201), r.text
        async with override_async_session() as db:
            task = await _created_task(db, 'no pref')
        assert task.ai_profile_id == DEFAULT_PROFILE_ID


class TestHumanCreatedTaskUnaffected:
    """A human creator keeps their own default — no fall-through."""

    async def test_admin_client_uses_own_default(
        self, admin_client, override_async_session, admin_user
    ):
        async with override_async_session() as db:
            owner = await db.get(User, admin_user.id)
            owner.default_profile_id = 'creator-profile'
            await db.commit()
        r = await admin_client.post(
            '/api/tasks', json={'title': 'human task', 'defer_start': True}
        )
        assert r.status_code in (200, 201), r.text
        async with override_async_session() as db:
            task = await _created_task(db, 'human task')
        assert task.ai_profile_id == 'creator-profile'


class TestPlanningTaskUsesResolvedProfile:
    """spawn_planning_task authored plans on the empty-env profile."""

    async def _schedule(self, db, ai_profile_id, created_by):
        sched = Schedule(
            id=str(uuid.uuid4()),
            title='planned',
            description='d',
            schedule_type='minutes',
            schedule_time='00:00',
            interval_value=5,
            plan_mode=True,
            phase_mode=PhaseMode.SINGLE.value,
            ai_profile_id=ai_profile_id,
            created_by=created_by,
        )
        db.add(sched)
        await db.commit()
        await db.refresh(sched)
        return sched

    async def test_unpinned_plan_task_uses_owner_profile(
        self, override_async_session, mock_workspace, isolated_profiles
    ):
        async with override_async_session() as db:
            bot, _ = await _seed(db)
            # 'default' is the column default for an unpinned
            # schedule — the sentinel, not a Claude pin.
            sched = await self._schedule(db, DEFAULT_PROFILE_ID, AI_BOT_USER_ID)
            task = await spawn_planning_task(sched, bot, db)
            assert task.ai_profile_id == THIRD_PARTY

    async def test_pinned_plan_task_keeps_its_pin(
        self, override_async_session, mock_workspace, isolated_profiles
    ):
        async with override_async_session() as db:
            bot, _ = await _seed(db)
            sched = await self._schedule(db, 'pinned-profile', AI_BOT_USER_ID)
            task = await spawn_planning_task(sched, bot, db)
            assert task.ai_profile_id == 'pinned-profile'


class TestSharedResolver:
    """The helper both paths go through, and the schedule fire path."""

    async def test_bot_owner_falls_through_to_admin(
        self, override_async_session
    ):
        async with override_async_session() as db:
            bot, _ = await _seed(db)
            assert await resolve_owner_profile(bot, db) == THIRD_PARTY

    async def test_human_owner_keeps_own_default(self, override_async_session):
        async with override_async_session() as db:
            _, admin = await _seed(db)
            assert await resolve_owner_profile(admin, db) == THIRD_PARTY

    async def test_no_owner_expresses_nothing(self, override_async_session):
        async with override_async_session() as db:
            assert await resolve_owner_profile(None, db) is None

    async def test_bot_owned_schedule_fire_still_falls_through(
        self, override_async_session
    ):
        async with override_async_session() as db:
            await _seed(db)
            sched = Schedule(
                id=str(uuid.uuid4()),
                title='bot-owned',
                description='d',
                schedule_type='minutes',
                schedule_time='00:00',
                interval_value=5,
                plan_mode=False,
                phase_mode=PhaseMode.SINGLE.value,
                ai_profile_id=None,
                created_by=AI_BOT_USER_ID,
            )
            db.add(sched)
            await db.commit()
            assert await resolve_schedule_profile(sched, db) == THIRD_PARTY
