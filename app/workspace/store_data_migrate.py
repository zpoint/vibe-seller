"""One-shot store-data layout migration, run at workspace init.

Layout v1 (2026-06): run artifacts live OUTSIDE the knowledge tree,
month-bucketed:

  stores/<slug>/            — curated knowledge, flat files only
  store-data/<slug>/<area>/<YYYY-MM>/<dated file>
  store-data/<slug>/<area>/<cross-run working file>

Old deployments upgrade in place on the first boot after the version
bump: (1) subdirectories under ``stores/<slug>/`` are run data by
definition (L3 knowledge is flat at the slug root) and move wholesale
to ``store-data/<slug>/``; (2) files whose NAME carries a date and
that are not already inside a ``YYYY-MM`` dir move into the bucket of
their own date (never the current month — old artifacts belong to
their own month). Undated files are cross-run working files and stay
put. A marker file keeps the check O(1) on every later boot.
"""

import logging
from pathlib import Path
import re
import shutil

logger = logging.getLogger(__name__)

LAYOUT_VERSION = 1
_MARKER_NAME = '.store-data-layout-version'
_MONTH_DIR = re.compile(r'^\d{4}-\d{2}$')
_FILE_DATE = re.compile(r'(20\d{2})[-_]?(\d{2})[-_]?(\d{2})')
_SKIP_PARTS = {'.git', '__pycache__'}


def _file_month(name: str) -> str | None:
    """Month bucket from a filename's own date, e.g. 2026-06."""
    m = _FILE_DATE.search(name)
    if not m:
        return None
    year, month, day = m.groups()
    if not (1 <= int(month) <= 12 and 1 <= int(day) <= 31):
        return None
    return f'{year}-{month}'


def _relocate_store_subdirs(root: Path, moved: list[str]) -> None:
    """stores/<slug>/<subdir>/ → store-data/<slug>/<subdir>/."""
    stores = root / 'stores'
    if not stores.is_dir():
        return
    for slug_dir in sorted(stores.iterdir()):
        if not slug_dir.is_dir() or slug_dir.name.startswith('.'):
            continue
        for sub in sorted(slug_dir.iterdir()):
            if not sub.is_dir() or sub.name in _SKIP_PARTS:
                continue
            dest = root / 'store-data' / slug_dir.name / sub.name
            if dest.exists():
                # Merge file-by-file; existing files win.
                for f in sorted(sub.rglob('*')):
                    if not f.is_file():
                        continue
                    target = dest / f.relative_to(sub)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not target.exists():
                        shutil.move(str(f), str(target))
                shutil.rmtree(sub, ignore_errors=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(sub), str(dest))
            moved.append(f'{sub.relative_to(root)} -> {dest.relative_to(root)}')


def _bucket_dated_files(store_data: Path, moved: list[str]) -> None:
    """Loose dated files → sibling <YYYY-MM>/ from their own date."""
    for f in sorted(store_data.rglob('*')):
        if not f.is_file():
            continue
        if any(p in _SKIP_PARTS for p in f.parts):
            continue
        if _MONTH_DIR.match(f.parent.name):
            continue  # already bucketed
        month = _file_month(f.name)
        if not month:
            continue  # undated working file — stays at the area root
        dest_dir = f.parent / month
        dest_dir.mkdir(exist_ok=True)
        dest = dest_dir / f.name
        if dest.exists():
            continue
        shutil.move(str(f), str(dest))
        moved.append(f'{f.name} -> {f.parent.name}/{month}/')


def migrate_store_data(root: Path) -> dict:
    """Idempotent layout upgrade; marker-gated to O(1) per boot."""
    marker = root / _MARKER_NAME
    try:
        if int(marker.read_text().strip()) >= LAYOUT_VERSION:
            return {'migrated': False, 'moved': []}
    except (FileNotFoundError, ValueError):
        pass

    moved: list[str] = []
    store_data = root / 'store-data'
    store_data.mkdir(parents=True, exist_ok=True)
    _relocate_store_subdirs(root, moved)
    _bucket_dated_files(store_data, moved)
    marker.write_text(f'{LAYOUT_VERSION}\n')
    if moved:
        logger.info(
            'store-data layout v%d migration moved %d item(s): %s',
            LAYOUT_VERSION,
            len(moved),
            '; '.join(moved[:20]),
        )
    return {'migrated': True, 'moved': moved}
