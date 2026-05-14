"""One-shot migration: rename on-disk store dirs whose slug changed.

Run with the server stopped. Idempotent and re-runnable — for each
store in the DB we compute the new slug with the current algorithm
(``store_slug(name, id)``) and compare against the directories under
``~/.vibe-seller/stores``, ``bin``, and ``browser_profiles``. Any
mismatched directory is moved to the new slug; targets that already
exist are skipped.

Motivation: the earlier ``store_slug`` preserved Unicode via
``re.UNICODE``, so a non-ASCII store name flowed straight into paths
and into the ``--session`` passed to browser-use. browser-use's
``validate_session_name`` is ASCII-only, so such stores could never
launch. The new algorithm drops non-ASCII chars (with a stable
``store-<id_prefix>`` fallback); this script lines up the filesystem
with it.
"""

import asyncio
import re
import shutil
import sys

from sqlalchemy import select

from app.browser.manager import store_slug
from app.config import VIBE_SELLER_DIR
from app.database import async_session
from app.models.store import Store

_DIRS_TO_CHECK = [
    VIBE_SELLER_DIR / 'stores',
    VIBE_SELLER_DIR / 'bin',
    VIBE_SELLER_DIR / 'browser_profiles',
]


def _legacy_slug(name: str) -> str:
    """Reproduce the pre-fix algorithm so we can find old directories.

    The previous ``store_slug`` used ``re.UNICODE`` in ``\\w`` and
    never fell back to an id-derived slug. Names that included a
    mix of ASCII and CJK characters produced e.g. ``abc-混合-name``
    rather than ``abc-name``. Enumerating that exact shape here lets
    the migration find those directories even though the live code
    would no longer emit them.
    """
    slug = re.sub(r'[^\w-]', '-', name.lower(), flags=re.UNICODE)
    slug = re.sub(r'_', '-', slug)
    slug = re.sub(r'-+', '-', slug).strip('-')
    return slug or name.lower().replace(' ', '-')


def _move(old: 'shutil.os.PathLike', new: 'shutil.os.PathLike') -> str:
    old_p, new_p = str(old), str(new)
    if not shutil.os.path.exists(old_p):
        return f'skip (missing): {old_p}'
    if shutil.os.path.exists(new_p):
        return f'skip (target exists): {old_p} -> {new_p}'
    shutil.move(old_p, new_p)
    return f'moved: {old_p} -> {new_p}'


async def _collect_moves() -> list[tuple[str, str, str]]:
    """Return [(store_name, store_id, plan_lines)] for each store."""
    plans: list[tuple[str, str, str]] = []
    async with async_session() as db:
        result = await db.execute(select(Store))
        stores = result.scalars().all()

    for store in stores:
        new = store_slug(store.name, store.id)
        # Enumerate every shape a previous algorithm could have
        # written to disk: the legacy UNICODE-preserving slug, the
        # raw name, a lower-cased name, and the new slug itself.
        candidates = {
            store.name,
            store.name.lower(),
            _legacy_slug(store.name),
            new,
        }
        lines = []
        for parent in _DIRS_TO_CHECK:
            for old_slug in candidates:
                if old_slug == new:
                    continue
                old_dir = parent / old_slug
                if old_dir.exists():
                    new_dir = parent / new
                    lines.append(_move(old_dir, new_dir))
            # Also handle `{slug}-aux` under browser_profiles.
            if parent.name == 'browser_profiles':
                for old_slug in candidates:
                    if old_slug == new:
                        continue
                    aux_old = parent / f'{old_slug}-aux'
                    if aux_old.exists():
                        lines.append(_move(aux_old, parent / f'{new}-aux'))
        if lines:
            plans.append((store.name, store.id, '\n  '.join(lines)))
    return plans


async def main() -> int:
    print('Scanning stores for slug mismatches…')
    plans = await _collect_moves()
    if not plans:
        print('No directories need migration — you are up to date.')
        return 0
    for name, sid, detail in plans:
        print(f'\nstore={name!r} id={sid}')
        print(f'  {detail}')
    print('\nDone.')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
