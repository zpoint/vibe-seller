"""A store's folder name (slug) is stored, unique, and never re-derived.

Everything a store owns on disk and in the browser layer is keyed by its
slug. It used to be recomputed from the name by dropping non-ASCII, so
「示例甲1店」 and 「示例乙1店」 were both ``1``: the second store was
refused — or, before that guard, shared the first one's folder, wrapper
and browser. See app/store_slugs.py.
"""

import os
from pathlib import Path
import sqlite3
import uuid

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import Session

from app.browser.wrapper import STORE_SLUG_RE, write_browser_use_wrapper
from app.database import upgrade_schema
from app.models.base import Base
from app.models.store import Store
from app.models.task import Task
from app.store_slugs import MAX_SLUG, legacy_store_slug, new_store_slug

pytestmark = pytest.mark.unit

ID_A = '3f2a9c1b-0000-4000-8000-000000000001'
ID_B = '7d41e0aa-0000-4000-8000-000000000002'

# Names a seller actually types, and ones nobody should but someone will.
EXTREME_NAMES = [
    '示例甲1店',
    '示例乙1店',
    'xx1店',
    'xx2店',
    'xy1店',
    'xy店',
    '店1',
    '1店',
    '1',
    '悦互联店',
    '东京ストア',
    'متجر 1',
    'Café Shop',
    'Cafe Shop',
    'Acme 沙特',
    'Acme 阿联酋',
    'Acme',
    'Acme!',
    'acme shop',
    'Shop Aux',
    'shop',
    '🛒 Shop',
    'a/b',
    '../evil',
    'C:\\shop',
    'CON',
    'aux',
    'store',
    'x' * 200,
    '店' * 100,
    '空格 店铺',
]


# ── new_store_slug ───────────────────────────────────────────────────


def test_two_chinese_names_with_one_digit_get_different_slugs():
    """The reported bug: both names reduced to "1"."""
    a = new_store_slug('示例甲1店', ID_A, taken=set())
    b = new_store_slug('示例乙1店', ID_B, taken={a})
    assert a == '1'  # what it always was
    assert b == '1-7d41e0'


def test_a_pure_ascii_name_keeps_its_readable_slug():
    assert new_store_slug('Acme Shop', ID_A, taken=set()) == 'acme-shop'


def test_any_name_keeps_the_old_slug_while_it_is_free():
    """Folder names look exactly as before; only a taken one changes."""
    for name in ('Acme Shop', 'Acme 沙特', '示例甲1店', '悦互联店', 'a/b'):
        assert new_store_slug(name, ID_A, taken=set()) == legacy_store_slug(
            name, ID_A
        )


def test_a_taken_readable_slug_gets_the_id():
    assert new_store_slug('Acme!', ID_B, taken={'acme'}) == 'acme-7d41e0'


@pytest.mark.parametrize('name', ['CON', 'aux', 'Nul', 'COM1', 'lpt9'])
def test_windows_reserved_names_get_the_id(name):
    """Windows cannot create a folder named CON/AUX/NUL/COMn/LPTn."""
    slug = new_store_slug(name, ID_A, taken=set())
    assert slug.endswith('-3f2a9c')


def test_a_slug_never_takes_another_stores_session_name():
    """A store's sessions are <slug>, <slug>-aux and <slug>-<task8>: a
    store called "Shop Aux" next to "Shop" would answer to Shop's aux
    session (and the other way round)."""
    assert new_store_slug('Shop Aux', ID_A, taken={'shop'}) != 'shop-aux'
    assert new_store_slug('Shop', ID_A, taken={'shop-aux'}) != 'shop'
    assert (
        new_store_slug('shop deadbeef', ID_A, taken={'shop'}) != 'shop-deadbeef'
    )
    assert new_store_slug('Shop', ID_A, taken={'shop-deadbeef'}) != 'shop'


def test_the_id_suffix_is_never_an_8_hex_task_id():
    """The daemon reaper reads a trailing 8-hex segment as a task id: a
    bare session named after the store would be reaped as an orphan."""
    for name, taken in (('示例乙1店', {'1'}), ('Acme 沙特', {'acme'})):
        tail = new_store_slug(name, ID_A, taken).rsplit('-', 1)[1]
        assert tail == '3f2a9c'


def test_a_long_name_still_fits_a_browser_session_name():
    """browser-use caps a session name (<slug>-<task8>-w<N>) at 64."""
    for name in ('x' * 200, 'Shop ' * 40):
        for taken in (set(), {'x' * MAX_SLUG}):
            slug = new_store_slug(name, ID_A, taken)
            assert len(f'{slug}-deadbeef-w9') <= 64, slug
    # A name that already fit keeps its whole slug.
    assert new_store_slug('y' * MAX_SLUG, ID_A, set()) == 'y' * MAX_SLUG


def test_every_extreme_name_gets_a_valid_unique_slug():
    taken: set[str] = set()
    for name in EXTREME_NAMES:
        slug = new_store_slug(name, str(uuid.uuid4()), taken)
        assert STORE_SLUG_RE.fullmatch(slug), (name, slug)
        assert slug not in taken, (name, slug)
        assert '/' not in slug and '\\' not in slug and '..' not in slug
        assert len(f'{slug}-deadbeef-w9') <= 64, (name, slug)
        taken.add(slug)


# ── a name is written into a shell script ───────────────────────────


def test_a_line_break_in_a_name_never_runs_as_a_command(tmp_path, monkeypatch):
    """The name goes into a comment line of the browser-use wrapper; a
    line break ended the comment and the rest ran as a command. Names are
    saved as typed, so the wrapper writer is where this is closed."""
    monkeypatch.setattr('app.browser.wrapper._BIN_DIR', tmp_path / 'bin')
    monkeypatch.setattr(
        'app.browser.bu_binary.shutil.which',
        lambda x: '/usr/local/bin/browser-use',
    )
    name = 'Shop\ntouch /tmp/pwned\r\nrm -rf ~'
    write_browser_use_wrapper(
        name, 'chrome', 9222, store_id=ID_A, slug='shop-touch-tmp-pwned'
    )
    script = tmp_path / 'bin' / 'shop-touch-tmp-pwned' / 'browser-use'
    lines = script.read_text().splitlines()
    assert not any(line.startswith(('touch', 'rm -rf')) for line in lines), (
        lines[:4]
    )
    assert lines[1].startswith('# Auto-generated browser-use wrapper')


# ── the model assigns it ─────────────────────────────────────────────


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "t.db"}')
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_every_inserted_store_gets_a_unique_slug(db):
    """Including several in ONE flush, whose INSERTs have not run when
    the next one's slug is picked."""
    db.add_all([Store(name=n) for n in EXTREME_NAMES])
    db.commit()
    slugs = db.scalars(select(Store.slug)).all()
    assert len(slugs) == len(EXTREME_NAMES)
    assert len(set(slugs)) == len(slugs)
    assert all(STORE_SLUG_RE.fullmatch(s) for s in slugs)


def test_the_database_refuses_a_duplicate_slug(db):
    db.add(Store(name='a', slug='same'))
    db.commit()
    db.add(Store(name='b', slug='same'))
    with pytest.raises(IntegrityError):
        db.commit()


# ── the boot migration (an install from before the column) ───────────


def _old_install(tmp_path: Path, rows: list[tuple[str, str, str]]) -> Path:
    """A database shaped like one written by the previous release:
    no ``stores.slug`` column, stores and their tasks in it."""
    path = tmp_path / 'vibe_seller.db'
    engine = create_engine(f'sqlite:///{path}')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        for store_id, name, created in rows:
            db.add(
                Store(
                    id=store_id,
                    name=name,
                    created_at=created,
                    updated_at=created,
                )
            )
            db.add(
                Task(
                    id=f'task-{store_id[:8]}',
                    store_id=store_id,
                    title=f'task of {name}',
                    status='completed',
                    created_by='u',
                )
            )
        db.commit()
    engine.dispose()
    con = sqlite3.connect(path)
    con.execute('DROP INDEX ix_stores_slug')
    con.execute('ALTER TABLE stores DROP COLUMN slug')
    con.commit()
    con.close()
    return path


async def _boot(db_path: Path, root: Path):
    """What every server start does to the database (``init_db``)."""
    engine = create_async_engine(f'sqlite+aiosqlite:///{db_path}')
    try:
        return await upgrade_schema(engine, root)
    finally:
        await engine.dispose()


def _slugs(db_path: Path) -> dict[str, str]:
    con = sqlite3.connect(db_path)
    try:
        return dict(con.execute('SELECT name, slug FROM stores'))
    finally:
        con.close()


OLD_STORES = [
    ('aaaaaaaa-1111-4000-8000-000000000001', 'Acme Shop', '2026-01-01'),
    ('bbbbbbbb-2222-4000-8000-000000000002', '悦互联店', '2026-01-02'),
    ('cccccccc-3333-4000-8000-000000000003', '示例甲1店', '2026-01-03'),
    ('dddddddd-4444-4000-8000-000000000004', 'xx2店', '2026-01-04'),
    ('eeeeeeee-5555-4000-8000-000000000005', 'Acme 沙特', '2026-01-05'),
    ('ffffffff-6666-4000-8000-000000000006', '空格 店铺', '2026-01-06'),
]


async def test_upgrade_keeps_every_store_in_the_folder_it_had(tmp_path):
    db_path = _old_install(tmp_path, OLD_STORES)
    root = tmp_path / 'home'
    for store_id, name, _ in OLD_STORES:
        d = root / 'stores' / legacy_store_slug(name, store_id)
        d.mkdir(parents=True)
        (d / 'notes.md').write_text(f'# Notes for {name}\n', 'utf-8')

    plan = await _boot(db_path, root)

    assert not any(a.shared_from for a in plan)
    assert _slugs(db_path) == {
        'Acme Shop': 'acme-shop',
        '悦互联店': 'store-bbbbbbbb',
        '示例甲1店': '1',
        'xx2店': 'xx2',
        'Acme 沙特': 'acme',
        '空格 店铺': 'store-ffffffff',
    }
    for name, slug in _slugs(db_path).items():
        notes = root / 'stores' / slug / 'notes.md'
        assert notes.read_text('utf-8') == f'# Notes for {name}\n'


async def test_upgrade_keeps_every_task_on_its_store(tmp_path):
    db_path = _old_install(tmp_path, OLD_STORES)
    await _boot(db_path, tmp_path / 'home')
    engine = create_engine(f'sqlite:///{db_path}')
    with Session(engine) as db:
        for store_id, name, _ in OLD_STORES:
            store = db.get(Store, store_id)
            assert store.name == name and store.slug
            tasks = db.scalars(
                select(Task.title).where(Task.store_id == store_id)
            ).all()
            assert tasks == [f'task of {name}']
    engine.dispose()


async def test_upgrade_is_idempotent(tmp_path):
    db_path = _old_install(tmp_path, OLD_STORES)
    root = tmp_path / 'home'
    await _boot(db_path, root)
    before = _slugs(db_path)
    assert await _boot(db_path, root) == []
    assert _slugs(db_path) == before


async def test_upgrade_makes_the_column_unique(tmp_path):
    db_path = _old_install(tmp_path, OLD_STORES)
    await _boot(db_path, tmp_path / 'home')
    con = sqlite3.connect(db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "UPDATE stores SET slug = 'acme-shop' WHERE name = 'Acme 沙特'"
            )
    finally:
        con.close()


async def test_a_store_created_after_upgrade_avoids_the_old_slugs(tmp_path):
    """「示例乙1店」 next to an upgraded 「示例甲1店」 (slug "1"), and an
    ASCII store literally called "1"."""
    db_path = _old_install(tmp_path, OLD_STORES)
    await _boot(db_path, tmp_path / 'home')
    engine = create_engine(f'sqlite:///{db_path}')
    with Session(engine) as db:
        db.add_all([Store(name='示例乙1店'), Store(name='1')])
        db.commit()
        slugs = db.scalars(select(Store.slug)).all()
    engine.dispose()
    assert len(set(slugs)) == len(slugs) == len(OLD_STORES) + 2


SHARED = [
    # Older version, no collision guard: both computed slug "1".
    ('cccccccc-3333-4000-8000-000000000003', '示例甲1店', '2026-01-03'),
    ('99999999-7777-4000-8000-000000000007', '示例乙1店', '2026-02-01'),
]


def _shared_folders(root: Path) -> None:
    notes = root / 'stores' / '1' / 'notes.md'
    notes.parent.mkdir(parents=True)
    notes.write_text('# Notes for 示例甲1店\nshared notes\n', 'utf-8')
    report = root / 'store-data' / '1' / 'reports' / 'oct.csv'
    report.parent.mkdir(parents=True)
    report.write_text('a,b\n1,2\n')
    profile = root / 'browser_profiles' / '1'
    (profile / 'Default' / 'Cache').mkdir(parents=True)
    (profile / 'Default' / 'Cookies').write_bytes(b'logged-in')
    (profile / 'Default' / 'Cache' / 'blob').write_bytes(b'x' * 10)
    if os.name != 'nt':
        (profile / 'SingletonLock').symlink_to('example-host-12345')
    wrapper = root / 'bin' / '1' / 'browser-use'
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text('# Auto-generated browser-use wrapper for store: x\n')


async def test_stores_that_shared_a_folder_each_get_their_own(tmp_path):
    db_path = _old_install(tmp_path, SHARED)
    root = tmp_path / 'home'
    _shared_folders(root)

    plan = await _boot(db_path, root)

    slugs = _slugs(db_path)
    assert slugs['示例甲1店'] == '1'  # the older store keeps it
    newer = slugs['示例乙1店']
    assert newer == '1-999999'
    assert [a.shared_from for a in plan if a.shared_from] == ['1']
    # The newer store starts from a copy of everything it showed before.
    for owner in ('1', newer):
        assert 'shared notes' in (
            root / 'stores' / owner / 'notes.md'
        ).read_text('utf-8')
        assert (root / 'store-data' / owner / 'reports' / 'oct.csv').is_file()
        cookies = root / 'browser_profiles' / owner / 'Default' / 'Cookies'
        assert cookies.read_bytes() == b'logged-in'
    copy = root / 'browser_profiles' / newer
    assert not (copy / 'Default' / 'Cache').exists()
    assert not (copy / 'SingletonLock').is_symlink()
    # The wrapper may name either store: gone, rewritten on next launch.
    assert not (root / 'bin' / '1').exists()


async def test_an_interrupted_split_finishes_on_the_next_boot(
    tmp_path, monkeypatch
):
    """The copy runs before the slugs are written; a crash in between
    leaves no slug written, and the next boot plans and copies again."""
    db_path = _old_install(tmp_path, SHARED)
    root = tmp_path / 'home'
    _shared_folders(root)

    def crash(conn, plan):
        raise RuntimeError('power cut')

    monkeypatch.setattr('app.store_slugs.apply_store_slugs', crash)
    with pytest.raises(RuntimeError):
        await _boot(db_path, root)
    assert set(_slugs(db_path).values()) == {None}
    monkeypatch.undo()

    await _boot(db_path, root)
    assert _slugs(db_path) == {'示例甲1店': '1', '示例乙1店': '1-999999'}
    assert (root / 'stores' / '1-999999' / 'notes.md').is_file()


async def test_a_store_an_older_version_added_later_is_migrated_too(tmp_path):
    """Rolled back, a store created, upgraded again: the column exists
    but that row has no slug, and its old slug is already taken."""
    db_path = _old_install(tmp_path, SHARED[:1])
    root = tmp_path / 'home'
    await _boot(db_path, root)
    con = sqlite3.connect(db_path)
    con.execute(
        'INSERT INTO stores (id, name, browser_backend, browser_config,'
        ' platforms, countries, platform_countries, capabilities,'
        ' ads_store_keys, ads_authorized, created_at, updated_at) VALUES'
        " (?, '示例乙1店', 'chrome', '{}', '[]', '[]', '{}', '{}', '{}',"
        " 0, '2026-03-01', '2026-03-01')",
        (SHARED[1][0],),
    )
    con.commit()
    con.close()

    await _boot(db_path, root)
    assert _slugs(db_path) == {'示例甲1店': '1', '示例乙1店': '1-999999'}


async def test_text_query_matches_the_orm(tmp_path):
    """The before-insert hook reads taken slugs with raw SQL; make sure
    it sees what the ORM wrote."""
    engine = create_engine(f'sqlite:///{tmp_path / "t.db"}')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Store(name='1'))
        db.commit()
        db.add(Store(name='1'))
        db.commit()
        rows = db.execute(text('SELECT slug FROM stores')).scalars().all()
    engine.dispose()
    assert rows[0] == '1' and rows[1].startswith('1-')
