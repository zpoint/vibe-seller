"""Unit tests for the legacy-state import (app/sync/migrate.py).

The migration is what makes an upgrade invisible: an install that had
been syncing from its per-module files keeps its cadence instead of
re-polling the world at once, and nothing on disk is destroyed that a
downgraded client still reads.
"""

import asyncio
import json

import pytest

from app.sync import migrate
from tests.fixtures.fake_sync_state import FakeState

pytestmark = pytest.mark.unit

LEGACY_SKILLS_META = {
    'last_sync_at': '2026-11-01T02:03:04+00:00',
    'last_commit': 'a' * 40,
    'status': 'success',
    'error': None,
    'copied': 3,
    'skipped': 7,
    'synced_skills': ['widget-006', 'acme-ads'],
    'gws_installed': True,
    'installed_deps': {'widget-006': 'abc'},
}


@pytest.fixture
def state(monkeypatch):
    return FakeState().install(monkeypatch)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(migrate, 'VIBE_SELLER_DIR', tmp_path)
    return tmp_path


def write(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding='utf-8')


# ── skills ────────────────────────────────────────────────────────


async def test_skills_schedule_state_moves_and_inventory_stays(home, state):
    path = home / '.claude' / 'skills' / '.sync_meta.json'
    write(path, LEGACY_SKILLS_META)

    assert await migrate.migrate_legacy_state() == {
        'skills': True,
        'knowledge': False,
        'presets': False,
    }

    row = await state.get_dict('skills')
    assert row['last_ok_at'] == '2026-11-01T02:03:04+00:00'
    assert row['version'] == 'a' * 40
    assert row['status'] == 'success'
    assert row['detail'] == {'copied': 3, 'skipped': 7}

    remaining = json.loads(path.read_text())
    assert set(remaining) == {
        'synced_skills',
        'gws_installed',
        'installed_deps',
    }
    assert remaining['synced_skills'] == ['widget-006', 'acme-ads']
    assert remaining['gws_installed'] is True


async def test_the_trimmed_file_still_reads_for_a_downgraded_client(
    home, state
):
    """Downgrade: the old code finds an inventory-only file and just
    syncs once. Nothing it reads is destroyed."""
    path = home / '.claude' / 'skills' / '.sync_meta.json'
    write(path, LEGACY_SKILLS_META)
    await migrate.migrate_legacy_state()

    remaining = json.loads(path.read_text())  # old _read_sync_meta()
    assert remaining.get('last_sync_at') is None  # cooldown gone → syncs
    assert remaining.get('synced_skills') == ['widget-006', 'acme-ads']


async def test_reimport_is_idempotent(home, state):
    path = home / '.claude' / 'skills' / '.sync_meta.json'
    write(path, LEGACY_SKILLS_META)
    await migrate.migrate_legacy_state()
    first = await state.get_dict('skills')

    write(path, LEGACY_SKILLS_META)  # an old file comes back somehow
    assert (await migrate.migrate_legacy_state())['skills'] is False
    assert (await state.get_dict('skills')) == first


async def test_the_trim_uses_a_fresh_read_after_the_await(
    home, state, monkeypatch
):
    """#204: the trim re-reads the file after the DB-import await. A
    concurrent writer (the boot-time dep install recording a new dep
    hash) must survive the trim — only the schedule keys go."""
    path = home / '.claude' / 'skills' / '.sync_meta.json'
    write(path, LEGACY_SKILLS_META)

    entered, gate = asyncio.Event(), asyncio.Event()
    real_import = migrate._import_row

    async def gated_import(job_name: str, meta: dict) -> bool:
        entered.set()
        await gate.wait()
        return await real_import(job_name, meta)

    monkeypatch.setattr(migrate, '_import_row', gated_import)

    task = asyncio.create_task(migrate.migrate_skills_meta())
    await asyncio.wait_for(entered.wait(), timeout=5)

    # The dep install lands a new hash while the import is parked.
    blob = json.loads(path.read_text(encoding='utf-8'))
    blob['installed_deps'] = {'widget-006': 'newhash'}
    path.write_text(json.dumps(blob), encoding='utf-8')

    gate.set()
    assert await asyncio.wait_for(task, timeout=5) is True

    remaining = json.loads(path.read_text(encoding='utf-8'))
    assert set(remaining) == {
        'synced_skills',
        'gws_installed',
        'installed_deps',
    }
    assert remaining['installed_deps'] == {'widget-006': 'newhash'}, (
        'the concurrent dep write was clobbered by the trim'
    )


async def test_an_existing_row_is_never_overwritten(home, state):
    state.seed('skills', last_ok_at='2027-01-01T00:00:00+00:00')
    write(
        home / '.claude' / 'skills' / '.sync_meta.json',
        LEGACY_SKILLS_META,
    )
    assert (await migrate.migrate_legacy_state())['skills'] is False
    assert (await state.get_dict('skills'))['last_ok_at'] == (
        '2027-01-01T00:00:00+00:00'
    )


# ── knowledge ─────────────────────────────────────────────────────


async def test_knowledge_meta_is_imported_then_deleted(home, state):
    path = home / 'knowledge' / '.sync_meta.json'
    write(path, LEGACY_SKILLS_META)

    assert (await migrate.migrate_legacy_state())['knowledge'] is True
    row = await state.get_dict('knowledge')
    assert row['version'] == 'a' * 40
    assert row['last_ok_at'] == '2026-11-01T02:03:04+00:00'
    assert not path.exists()


# ── provider presets ──────────────────────────────────────────────


async def test_presets_bookkeeping_moves_and_the_catalog_stays(home, state):
    path = home / 'cache' / 'provider_presets.json'
    write(
        path,
        {
            'sha256': 'b' * 64,
            'etag': 'W/"v1"',
            'fetched_at': '2026-11-02T00:00:00+00:00',
            'source_url': 'https://example.com/presets.py',
            'presets': {'acme_test': {'name': 'Acme'}},
            'models': {'acme_test': []},
        },
    )

    assert (await migrate.migrate_legacy_state())['presets'] is True
    row = await state.get_dict('presets')
    assert row['version'] == 'b' * 64
    assert row['etag'] == 'W/"v1"'
    assert row['last_ok_at'] == '2026-11-02T00:00:00+00:00'
    assert row['detail'] == {
        'source_url': 'https://example.com/presets.py',
    }

    # The catalog is untouched and still readable.
    blob = json.loads(path.read_text())
    assert set(blob) == {'presets', 'models'}
    assert blob['presets']['acme_test']['name'] == 'Acme'


async def test_a_bookkeeping_only_cache_file_does_not_break_boot(home, state):
    path = home / 'cache' / 'provider_presets.json'
    write(path, {'sha256': 'c' * 64})
    assert (await migrate.migrate_legacy_state())['presets'] is True
    assert (await state.get_dict('presets'))['version'] == 'c' * 64


# ── the boring cases ──────────────────────────────────────────────


async def test_corrupt_partial_and_missing_files_are_never_synced(home, state):
    (home / '.claude' / 'skills').mkdir(parents=True)
    (home / '.claude' / 'skills' / '.sync_meta.json').write_text(
        '{not json', encoding='utf-8'
    )
    (home / 'knowledge').mkdir(parents=True)
    (home / 'knowledge' / '.sync_meta.json').write_text('[]', encoding='utf-8')
    (home / 'cache').mkdir(parents=True)
    (home / 'cache' / 'provider_presets.json').write_text('', encoding='utf-8')

    assert await migrate.migrate_legacy_state() == {
        'skills': False,
        'knowledge': False,
        'presets': False,
    }
    assert await state.get_dict('skills') is None


async def test_a_file_with_no_schedule_state_imports_nothing(home, state):
    """Inventory only — "never synced", not "synced at the epoch"."""
    write(
        home / '.claude' / 'skills' / '.sync_meta.json',
        {'synced_skills': ['widget-006']},
    )
    assert (await migrate.migrate_legacy_state())['skills'] is False
    assert await state.get_dict('skills') is None


async def test_a_fresh_install_is_a_noop(home, state):
    assert await migrate.migrate_legacy_state() == {
        'skills': False,
        'knowledge': False,
        'presets': False,
    }
    assert await state.get_all() == {}


# ── a legacy FAILED run must not be imported as success ────────────


async def test_a_failed_legacy_run_is_not_imported_as_success(home, state):
    """The old writers recorded failures too — with a ``last_commit``
    whose content never landed. Importing that as ``last_ok_at`` +
    ``version`` would count the failure as a sync AND make the next
    probe answer "nothing changed" for content we never got."""
    path = home / '.claude' / 'skills' / '.sync_meta.json'
    write(
        path,
        {
            'last_sync_at': '2026-11-01T02:03:04+00:00',
            'last_commit': 'a' * 40,
            'status': 'failed',
            'error': 'Remote skills sync error: boom',
            'copied': 0,
            'skipped': 0,
        },
    )

    assert (await migrate.migrate_legacy_state())['skills'] is True
    row = await state.get_dict('skills')
    assert row['last_ok_at'] is None
    assert row['version'] is None
    assert row['last_run_at'] == '2026-11-01T02:03:04+00:00'
    assert row['status'] == 'failed'
    assert 'boom' in row['error']


async def test_a_failed_knowledge_run_is_not_imported_as_success(home, state):
    path = home / 'knowledge' / '.sync_meta.json'
    write(
        path,
        {
            'last_sync_at': '2026-11-01T02:03:04+00:00',
            'last_commit': 'a' * 40,
            'status': 'failed',
            'error': 'Remote sync error: boom',
        },
    )

    assert (await migrate.migrate_legacy_state())['knowledge'] is True
    row = await state.get_dict('knowledge')
    assert row['last_ok_at'] is None
    assert row['version'] is None
    assert row['last_run_at'] == '2026-11-01T02:03:04+00:00'


async def test_a_file_without_a_status_counts_as_success(home, state):
    """Old enough to predate the field — and those runs only ever wrote
    the file when the sync landed."""
    write(
        home / '.claude' / 'skills' / '.sync_meta.json',
        {'last_sync_at': '2026-11-01T02:03:04+00:00', 'last_commit': 'a' * 40},
    )
    assert (await migrate.migrate_legacy_state())['skills'] is True
    row = await state.get_dict('skills')
    assert row['last_ok_at'] == '2026-11-01T02:03:04+00:00'
    assert row['version'] == 'a' * 40
