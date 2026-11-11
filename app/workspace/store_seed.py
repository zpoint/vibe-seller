"""Seed files written at store creation.

Kept out of ``manager.py`` to (a) hold the file under its 800-line
limit and (b) make the catalog-stub contract testable in isolation.
"""

from pathlib import Path


def write_catalog_stub(store_dir: Path, slug: str, name: str) -> Path:
    """Write a minimal ``CATALOG.md`` so turn 1 of a brand-new store has
    a catalog to read.

    Idempotent: skips if a real catalog already exists (the L3
    catalog-sync scheduled job overwrites this stub later with
    actual content). Returns the catalog path.
    """
    catalog_md = store_dir / 'CATALOG.md'
    if catalog_md.exists():
        return catalog_md
    catalog_md.write_text(
        f'# Store Catalog — {name} (stub)\n\n'
        '| File | Relevance | Summary |\n|---|---|---|\n'
        f'| stores/{slug}/STORE.md | profile | Store profile |\n',
        encoding='utf-8',
    )
    return catalog_md
