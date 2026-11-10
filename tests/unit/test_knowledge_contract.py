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

    def test_a_store_standard_is_never_deleted(self):
        notes = NOTE + 'acos_no_lower: 20\n'
        err = kc.deletion_only_error(notes, NOTE)
        assert err and 'acos_no_lower: 20' in err
        # Removing the platform note beside it is still fine.
        assert (
            kc.deletion_only_error(
                notes,
                notes.replace(
                    'The first submit is refused by the reviewer; that is normal.\n',
                    '',
                ),
            )
            is None
        )

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

    def test_a_differently_cased_path_is_the_same_tier(self):
        """macOS resolves `Stores/` to `stores/`: the same store's file."""
        assert kc.tier_error('Stores/ACME/notes.md', 'acme') is None
        assert kc.tier_error('Stores/other/notes.md', 'acme')

    def test_store_run_cleans_its_own_store_only(self):
        assert kc.tier_error('stores/acme-x/notes.md', 'acme-x') is None
        assert kc.tier_error('stores/beta-y/notes.md', 'acme-x')
        assert kc.tier_error('knowledge/notes.md', 'acme-x')
        assert kc.tier_error('../stores/acme-x/notes.md', 'acme-x')
        assert kc.tier_error('stores/acme-x/../beta-y/notes.md', 'acme-x')
        assert kc.tier_error('knowledge/../stores/acme-x/notes.md', None)
        assert kc.tier_error('stores/acme-x/sub/../notes.md', 'acme-x') is None


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


class TestOutcomeIsHeldToDeletions:
    """Observed: a cleanup run edited a note directly with a Python script,
    trimming half-lines out of wrapped paragraphs. The endpoint never saw
    the write; the outcome check must."""

    def _tier(self, tmp_path):
        store = tmp_path / 'stores' / 'acme-x'
        store.mkdir(parents=True)
        (store / 'notes.md').write_text(NOTE)
        (store / 'other.md').write_text('# Other\n\nkeep me\n')
        (store / 'CATALOG.md').write_text('| File |\n')
        return store

    def test_a_mid_line_trim_is_put_back(self, tmp_path):
        store = self._tier(tmp_path)
        snap = kc.snapshot_tier(tmp_path, 'acme-x')
        assert store / 'CATALOG.md' not in snap
        (store / 'notes.md').write_text(NOTE.replace('Business Reports', ''))
        assert kc.undo_non_deletions(snap) == [store / 'notes.md']
        assert (store / 'notes.md').read_text() == NOTE

    def test_an_edit_someone_else_made_meanwhile_is_kept(self, tmp_path):
        """Another task (or the user) writes the store's notes while its
        cleanup runs: the restore cannot tell that edit from a bad one,
        so it leaves the file alone instead of undoing the new standard."""
        store = self._tier(tmp_path)
        kc.watch_writes('cleaner')
        snap = kc.snapshot_tier(tmp_path, 'acme-x')
        (store / 'notes.md').write_text(NOTE + 'acos_no_lower: 30\n')
        kc.note_write('stores/acme-x/notes.md', 'other-task')
        kc.note_write('stores/acme-x/notes.md', 'cleaner')  # its own: no
        others = kc.end_watch('cleaner')
        assert kc.undo_non_deletions(snap, others) == []
        assert 'acos_no_lower: 30' in (store / 'notes.md').read_text()

    def test_line_deletions_and_untouched_files_stay(self, tmp_path):
        store = self._tier(tmp_path)
        snap = kc.snapshot_tier(tmp_path, 'acme-x')
        cleaned = NOTE.replace(
            'The first submit is refused by the reviewer; that is normal.\n',
            '',
        )
        (store / 'notes.md').write_text(cleaned)
        assert kc.undo_non_deletions(snap) == []
        assert (store / 'notes.md').read_text() == cleaned

    def test_global_tier_skips_the_shipped_project_tree(self, tmp_path):
        (tmp_path / 'knowledge' / 'project').mkdir(parents=True)
        (tmp_path / 'knowledge' / 'notes.md').write_text(NOTE)
        (tmp_path / 'knowledge' / 'project' / 'a.md').write_text('x\n')
        snap = kc.snapshot_tier(tmp_path, None)
        assert set(snap) == {tmp_path / 'knowledge' / 'notes.md'}
