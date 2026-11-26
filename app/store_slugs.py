"""A store's folder name (slug): stored, unique, never re-derived.

Everything a store owns on disk and in the browser layer is keyed by its
slug: ``stores/<slug>/`` (knowledge), ``store-data/<slug>/`` (run data),
the browser-use wrapper ``bin/<slug>/``, the browser profile, the CDP
session names. The slug used to be recomputed from the display name on
every use, by dropping everything that is not ASCII. For a Chinese name
that keeps only the digits: 「示例甲1店」 and 「示例乙1店」 both
became ``1``, so the second store was refused ("both reduce to the same
folder name '1'") — or, before that guard existed, silently shared the
first one's folder, wrapper and browser.

So the slug is a column (``Store.slug``), unique in the database, set
when the row is inserted (``app/models/store.py``) and on a rename
(``app/routers/stores.py``) — nowhere else. What users see does not
change: a store gets the same folder name as before whenever that name
is free (:func:`new_store_slug`); only where the old name was taken —
where the old code refused the store — is part of the store id appended
(「示例乙1店」 → ``1-7d41e0``).

Installs that predate the column are migrated on boot
(:func:`migrate_store_slugs`): each store is given exactly the slug the
old code computed, so every folder stays where it is. The one exception
is a group of stores an old version (before the collision guard) let
share one slug: the oldest keeps it, each other one gets its own slug
and a COPY of the shared folders, so neither loses what it showed
before the upgrade.
"""

import asyncio
from collections.abc import Collection
from dataclasses import dataclass
import logging
from pathlib import Path
import re
import shutil

logger = logging.getLogger(__name__)

# Names Windows refuses as a file or folder name, in any case. A store
# called "CON" or "Aux" would otherwise get a folder it cannot create.
_WINDOWS_RESERVED = frozenset(
    {'con', 'prn', 'aux', 'nul'}
    | {f'com{i}' for i in range(1, 10)}
    | {f'lpt{i}' for i in range(1, 10)}
)

# The tail of a store's browser-session name: ``aux`` or a task id[:8].
_SESSION_TAIL = re.compile(r'aux|[0-9a-f]{8}')

# Chrome writes these into a live profile; a copied lock makes the copy
# look in use by another process, and caches only cost copy time.
_PROFILE_SKIP = shutil.ignore_patterns(
    'Singleton*',
    'lockfile',
    'Cache',
    'Code Cache',
    'GPUCache',
    'DawnCache',
    'DawnGraphiteCache',
    'DawnWebGPUCache',
    'GrShaderCache',
    'ShaderCache',
)


#: Longest slug a store can be given. Its browser sessions are named
#: ``<slug>-<task id[:8]>-w<N>`` and browser-use refuses a session name
#: over 64 characters: 52 + 9 + 3 = 64. The old code had no cap, so a
#: longer name left the store with a browser it could not open.
MAX_SLUG = 52


def ascii_residue(name: str) -> str:
    """The ASCII letters/digits of *name*, lowercased, joined by ``-``."""
    slug = re.sub(r'[^a-zA-Z0-9_-]', '-', name.lower())
    slug = re.sub(r'_', '-', slug)
    return re.sub(r'-+', '-', slug).strip('-')


def legacy_store_slug(name: str, store_id: str | None = None) -> str:
    """The slug every version before the ``slug`` column derived.

    How the boot migration finds an existing store's folders, and what
    the name check in ``routers/stores.py`` compares. It is lossy — two
    different names can give the same result — so it never names a
    store's folder at runtime: read ``Store.slug``.
    """
    slug = ascii_residue(name)
    if slug:
        return slug
    if not store_id:
        raise ValueError(
            f'legacy_store_slug: name {name!r} reduces to an empty ASCII '
            'slug and no store_id was provided for fallback'
        )
    return f'store-{store_id[:8]}'


def _clashes(candidate: str, taken: Collection[str]) -> bool:
    """Would *candidate* share a name with another store, or with one of
    its browser sessions?

    A store's sessions are ``<slug>``, ``<slug>-aux`` and
    ``<slug>-<task id[:8]>`` (app/browser/wrapper.py), and the daemon
    reaper reads a trailing 8-hex segment as a task id. So a slug must
    differ from every taken one, and neither may be the other plus
    ``-aux`` or ``-<8 hex>`` — else one store's wrapper accepts the
    other's session name and drives its browser.
    """
    if candidate in taken or candidate in _WINDOWS_RESERVED:
        return True
    head, _, tail = candidate.rpartition('-')
    if head and head in taken and _SESSION_TAIL.fullmatch(tail):
        return True
    prefix = f'{candidate}-'
    return any(
        t.startswith(prefix) and _SESSION_TAIL.fullmatch(t[len(prefix) :])
        for t in taken
    )


def new_store_slug(name: str, store_id: str, taken: Collection[str]) -> str:
    """The slug for a store named *name* now, avoiding *taken*.

    The slug the old code derived (:func:`legacy_store_slug`) whenever
    it is free, so folder names look exactly as they always have. When
    it is taken (the case the old code refused, or let two stores
    share), reserved on Windows, or would clash with a session name,
    part of the store id is appended — unique because the id is. That
    suffix is 6 hex, never 8: a bare session named ``<slug>`` must not
    read as ``<other slug>-<task id[:8]>``.

    Capped at :data:`MAX_SLUG` characters, which only shortens a name
    whose old slug was too long for a browser session to work at all.
    """
    compact = re.sub(r'[^a-z0-9]', '', store_id.lower())
    residue = ascii_residue(name)
    base = residue or 'store'

    def fit(head: str, suffix: str = '') -> str:
        room = MAX_SLUG - (len(suffix) + 1 if suffix else 0)
        head = head[:room].rstrip('-')
        return f'{head}-{suffix}' if suffix else head

    for candidate in (
        fit(residue) if residue else legacy_store_slug(name, store_id),
        fit(base, compact[:6]),
        fit(base, compact[:10]),
        f'store-{compact}',
    ):
        if not _clashes(candidate, taken):
            return candidate
    raise ValueError(
        f'new_store_slug: no free slug for {name!r} (id {store_id!r})'
    )


# ── Boot migration ───────────────────────────────────────────────────


@dataclass(frozen=True)
class SlugAssignment:
    """The slug one store is given by the boot migration."""

    store_id: str
    name: str
    slug: str
    #: The slug this store shared with an older store before the
    #: upgrade (its folders are copied from there), else None.
    shared_from: str | None = None


def plan_store_slugs(conn) -> list[SlugAssignment]:
    """Decide a slug for every store row that has none. Reads only.

    Every store gets the slug the old code computed for it, so its
    folders stay where they are. When several stores computed the SAME
    slug (possible before the collision guard existed), the oldest keeps
    it and each other one gets a new slug of its own.
    """
    rows = conn.exec_driver_sql(
        'SELECT id, name, slug, created_at FROM stores'
    ).fetchall()
    taken = {row[2] for row in rows if row[2]}
    pending = sorted(
        (row for row in rows if not row[2]),
        key=lambda row: (row[3] or '', row[0]),
    )
    plan: list[SlugAssignment] = []
    shared: list[tuple[str, str, str]] = []
    for store_id, name, _slug, _created in pending:
        legacy = legacy_store_slug(name, store_id)
        if legacy in taken:
            shared.append((store_id, name, legacy))
            continue
        taken.add(legacy)
        plan.append(SlugAssignment(store_id, name, legacy))
    # Only after every store that can keep its old slug has it, so a new
    # slug never lands on one of those.
    for store_id, name, legacy in shared:
        slug = new_store_slug(name, store_id, taken)
        taken.add(slug)
        plan.append(SlugAssignment(store_id, name, slug, shared_from=legacy))
    return plan


def apply_store_slugs(conn, plan: list[SlugAssignment]) -> None:
    """Write the planned slugs and make the column unique.

    Only rows still without a slug are written, so a re-run (or a plan
    gone stale) never moves a store that already has one.
    """
    for a in plan:
        conn.exec_driver_sql(
            'UPDATE stores SET slug = ? '
            "WHERE id = ? AND (slug IS NULL OR slug = '')",
            (a.slug, a.store_id),
        )
    # Same name as the index create_all makes on a fresh database, so
    # this is a no-op there.
    conn.exec_driver_sql(
        'CREATE UNIQUE INDEX IF NOT EXISTS ix_stores_slug ON stores (slug)'
    )


def _copy_tree(src: Path, dst: Path, ignore=None) -> None:
    if not src.is_dir():
        return
    try:
        shutil.copytree(
            src, dst, symlinks=True, ignore=ignore, dirs_exist_ok=True
        )
    except (OSError, shutil.Error) as e:
        # Partial is better than none, and boot must go on: the store
        # still gets its own slug, only the copy is incomplete.
        logger.warning('store slug migration: copy %s → %s: %s', src, dst, e)


def split_shared_dirs(root: Path, splits: list[SlugAssignment]) -> None:
    """Give each store that shared a slug its own copy of the folders.

    The store that keeps the old slug keeps the originals. The wrapper
    left in ``bin/<old slug>/`` may name either store, so it is removed;
    the next task of its owner writes a fresh one (every launch does).
    """
    for a in splits:
        assert a.shared_from is not None
        logger.warning(
            'store %s (%r) shared folder %r with an older store; it now '
            'has its own, %r, starting from a copy',
            a.store_id,
            a.name,
            a.shared_from,
            a.slug,
        )
        for area in ('stores', 'store-data'):
            _copy_tree(root / area / a.shared_from, root / area / a.slug)
        _copy_tree(
            root / 'browser_profiles' / a.shared_from,
            root / 'browser_profiles' / a.slug,
            ignore=_PROFILE_SKIP,
        )
    for old in {a.shared_from for a in splits}:
        shutil.rmtree(root / 'bin' / str(old), ignore_errors=True)


async def migrate_store_slugs(engine, root: Path) -> list[SlugAssignment]:
    """Give every store that has none its slug. Idempotent; runs on boot.

    The folder copies run off the event loop, BEFORE the slugs are
    written: if the process dies in between, the next boot plans the same
    slugs again (the plan is a pure function of the rows) and the copy
    resumes into the folders already there.
    """
    async with engine.connect() as conn:
        plan = await conn.run_sync(plan_store_slugs)
    splits = [a for a in plan if a.shared_from]
    if splits:
        await asyncio.to_thread(split_shared_dirs, root, splits)
    async with engine.begin() as conn:
        await conn.run_sync(apply_store_slugs, plan)
    if plan:
        logger.info('store slug migration: %d store(s) given a slug', len(plan))
    return plan
