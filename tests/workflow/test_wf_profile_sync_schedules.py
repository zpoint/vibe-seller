"""Workflow tests: sync_profile_to_schedules pref on set-default.

The opt-in user pref ``sync_profile_to_schedules`` (default off)
controls whether ``PATCH /api/profiles/{id}/set-default`` also re-pins
the user's schedules that carry a concrete ``ai_profile_id`` to the
new default. Rows holding ``'default'``/NULL already inherit the live
default via ``resolve_schedule_profile()`` and must stay untouched;
other users' schedules are never in scope.
"""

import uuid

import pytest
from sqlalchemy import text

from app.models.schedule import Schedule
from app.models.schedule_constants import PhaseMode
from app.models.user import User
from app.password import hash_password

pytestmark = pytest.mark.workflow


async def _make_schedule(db, created_by, ai_profile_id):
    sched = Schedule(
        id=str(uuid.uuid4()),
        title=f'sched-{uuid.uuid4().hex[:8]}',
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


async def _get_schedule(db, sched_id):
    return await db.get(Schedule, sched_id)


class TestSyncPrefRoundtrip:
    async def test_pref_defaults_off(self, admin_client):
        me = await admin_client.get('/api/auth/me')
        assert me.json()['sync_profile_to_schedules'] is False

    async def test_pref_toggle_roundtrip(self, admin_client):
        r = await admin_client.patch(
            '/api/auth/me/profile',
            json={'sync_profile_to_schedules': True},
        )
        assert r.status_code == 200
        me = await admin_client.get('/api/auth/me')
        assert me.json()['sync_profile_to_schedules'] is True

        await admin_client.patch(
            '/api/auth/me/profile',
            json={'sync_profile_to_schedules': False},
        )
        me = await admin_client.get('/api/auth/me')
        assert me.json()['sync_profile_to_schedules'] is False


class TestSetDefaultSync:
    async def test_toggle_off_leaves_pinned_schedules(
        self, admin_client, admin_user, override_async_session
    ):
        """Default (off): set-default must not touch any schedule."""
        async with override_async_session() as db:
            pinned = await _make_schedule(db, admin_user.id, 'prof-old')
            inherit = await _make_schedule(db, admin_user.id, 'default')

        await admin_client.post(
            '/api/profiles',
            json={'id': 'prof-new', 'name': 'New', 'env': {}},
        )
        r = await admin_client.patch('/api/profiles/prof-new/set-default')
        assert r.status_code == 200
        assert r.json()['default_profile_id'] == 'prof-new'
        assert r.json()['schedules_synced'] == 0

        async with override_async_session() as db:
            assert (
                await _get_schedule(db, pinned.id)
            ).ai_profile_id == 'prof-old'
            assert (
                await _get_schedule(db, inherit.id)
            ).ai_profile_id == 'default'

    async def test_toggle_on_repins_only_concrete_pins(
        self, admin_client, admin_user, override_async_session
    ):
        """On: concrete pins move; 'default'/NULL inherit rows stay."""
        await admin_client.patch(
            '/api/auth/me/profile',
            json={'sync_profile_to_schedules': True},
        )
        async with override_async_session() as db:
            pinned = await _make_schedule(db, admin_user.id, 'prof-old')
            inherit_default = await _make_schedule(db, admin_user.id, 'default')
            # Legacy NULL row: the ORM column default forces 'default'
            # on insert, so simulate a pre-default DB with raw SQL.
            inherit_null = await _make_schedule(db, admin_user.id, 'x')
            await db.execute(
                text(
                    'UPDATE schedules SET ai_profile_id = NULL WHERE id = :sid'
                ),
                {'sid': inherit_null.id},
            )
            await db.commit()

        await admin_client.post(
            '/api/profiles',
            json={'id': 'prof-new', 'name': 'New', 'env': {}},
        )
        r = await admin_client.patch('/api/profiles/prof-new/set-default')
        assert r.status_code == 200
        assert r.json()['schedules_synced'] == 1

        async with override_async_session() as db:
            assert (
                await _get_schedule(db, pinned.id)
            ).ai_profile_id == 'prof-new'
            assert (
                await _get_schedule(db, inherit_default.id)
            ).ai_profile_id == 'default'
            assert (
                await _get_schedule(db, inherit_null.id)
            ).ai_profile_id is None

    async def test_toggle_on_never_touches_other_users(
        self, admin_client, admin_user, override_async_session
    ):
        """Sync is scoped to created_by = current user."""
        await admin_client.patch(
            '/api/auth/me/profile',
            json={'sync_profile_to_schedules': True},
        )
        async with override_async_session() as db:
            other = User(
                id=str(uuid.uuid4()),
                username='other',
                password_hash=hash_password('other123'),
                role='member',
                is_active=True,
            )
            db.add(other)
            await db.commit()
            mine = await _make_schedule(db, admin_user.id, 'prof-old')
            theirs = await _make_schedule(db, other.id, 'prof-old')

        await admin_client.post(
            '/api/profiles',
            json={'id': 'prof-new', 'name': 'New', 'env': {}},
        )
        r = await admin_client.patch('/api/profiles/prof-new/set-default')
        assert r.status_code == 200
        assert r.json()['schedules_synced'] == 1

        async with override_async_session() as db:
            assert (
                await _get_schedule(db, mine.id)
            ).ai_profile_id == 'prof-new'
            assert (
                await _get_schedule(db, theirs.id)
            ).ai_profile_id == 'prof-old'
