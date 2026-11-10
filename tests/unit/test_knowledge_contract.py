"""Unit tests for the one-time knowledge cleanup contract.

- ``deletion_only_error``: a cleanup write may only remove lines.
- ``init_db`` records where an install's knowledge stands: a fresh
  install owes nothing; an install that existed before the floor owes
  one cleanup; a later boot never moves an existing floor.
"""

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app import knowledge_contract as kc
from app.database import init_db
from app.models.app_settings import AppSettings
from app.models.base import Base

pytestmark = pytest.mark.unit

NOTE = (
    '# Notes\n'
    '\n'
    'margin: 40\n'
    'The first submit is refused by the reviewer; that is normal.\n'
    'Business Reports live under Reports.\n'
)


class TestDeletionOnly:
    def test_removing_a_passage_is_allowed(self):
        new = NOTE.replace(
            'The first submit is refused by the reviewer; that is normal.\n',
            '',
        )
        assert kc.deletion_only_error(NOTE, new) is None

    def test_blank_line_changes_are_allowed(self):
        assert kc.deletion_only_error(NOTE, NOTE.replace('\n\n', '\n')) is None

    def test_an_added_line_is_refused(self):
        err = kc.deletion_only_error(NOTE, NOTE + 'A brand new claim.\n')
        assert err and 'A brand new claim.' in err

    def test_a_reworded_line_is_refused(self):
        err = kc.deletion_only_error(NOTE, NOTE.replace('40', '30'))
        assert err and 'margin: 30' in err

    def test_reordering_is_refused(self):
        lines = NOTE.splitlines(keepends=True)
        swapped = ''.join(lines[:2] + [lines[3], lines[2]] + lines[4:])
        assert kc.deletion_only_error(NOTE, swapped) is not None


class TestTier:
    def test_global_run_cleans_shared_knowledge_only(self):
        assert kc.tier_error('knowledge/notes.md', None) is None
        assert kc.tier_error('./knowledge/notes.md', None) is None
        assert kc.tier_error('knowledge/project/common/a.md', None)
        assert kc.tier_error('stores/acme-x/notes.md', None)

    def test_store_run_cleans_its_own_store_only(self):
        assert kc.tier_error('stores/acme-x/notes.md', 'acme-x') is None
        assert kc.tier_error('stores/beta-y/notes.md', 'acme-x')
        assert kc.tier_error('knowledge/notes.md', 'acme-x')
        assert kc.tier_error('../stores/acme-x/notes.md', 'acme-x')


async def _db(monkeypatch):
    for k in ('ADMIN_EMAIL', 'ADMIN_PASSWORD', 'VIBE_AUTH_REQUIRED'):
        monkeypatch.delenv(k, raising=False)
    engine = create_async_engine(
        'sqlite+aiosqlite://',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr('app.database.engine', engine)
    monkeypatch.setattr('app.database.async_session', maker)
    return maker


async def _floor(maker):
    async with maker() as db:
        row = await db.get(AppSettings, kc.FLOOR_KEY)
        return row.value if row else None


class TestFloorAtBoot:
    async def test_a_fresh_install_owes_nothing(self, monkeypatch):
        maker = await _db(monkeypatch)
        await init_db()
        assert await _floor(maker) == str(kc.KNOWLEDGE_CONTRACT)
        async with maker() as db:
            assert not await kc.needs_cleanup(db, kc.L2_SCOPE)
            assert not await kc.needs_cleanup(db, kc.store_scope('s1'))

    async def test_an_install_from_before_owes_one_cleanup(self, monkeypatch):
        maker = await _db(monkeypatch)
        await init_db()  # the old install's first boot (catalog seeded)
        async with maker() as db:  # ...from a release with no floor
            await db.execute(
                delete(AppSettings).where(AppSettings.key == kc.FLOOR_KEY)
            )
            await db.commit()
        await init_db()  # the upgrade's boot
        assert await _floor(maker) == '1'
        async with maker() as db:
            assert await kc.needs_cleanup(db, kc.L2_SCOPE)
            assert await kc.needs_cleanup(db, kc.store_scope('s1'))
            await kc.mark_cleaned(db, kc.store_scope('s1'))
            await db.commit()
            assert not await kc.needs_cleanup(db, kc.store_scope('s1'))
            assert await kc.needs_cleanup(db, kc.store_scope('s2'))

    async def test_a_later_boot_never_moves_the_floor(self, monkeypatch):
        maker = await _db(monkeypatch)
        await init_db()
        async with maker() as db:
            row = await db.get(AppSettings, kc.FLOOR_KEY)
            row.value = '1'
            await db.commit()
        await init_db()
        assert await _floor(maker) == '1'

    async def test_no_floor_recorded_means_nothing_owed(self, monkeypatch):
        maker = await _db(monkeypatch)
        async with maker() as db:
            assert not await kc.needs_cleanup(db, kc.L2_SCOPE)
