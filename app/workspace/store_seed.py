"""Seed files written at store creation.

Kept out of ``manager.py`` to (a) hold the file under its 800-line
limit and (b) make the catalog-stub contract testable in isolation.
"""

from pathlib import Path


def write_catalog_stub(store_dir: Path, slug: str, name: str) -> Path:
    """Write a minimal ``CATALOG.md`` so the catalog-first hook can
    succeed on turn 1 of a brand-new store.

    Idempotent: skips if a real catalog already exists (the L3
    catalog-sync scheduled job overwrites this stub later with
    actual content). Returns the catalog path.

    See ``app/ai/bash_safety.py::should_mark_catalog_read`` for
    the read-side contract: the hook flips ``_catalog_read`` on
    any ``Read`` of a catalog-shaped path. With this stub the
    agent's first ``Read CATALOG.md`` succeeds immediately, which
    avoids a class of failures where the post-Read parallel
    ``Bash ls`` calls get hook-denied and the resulting assistant
    turn (thinking block + denied tool_use) malforms the next
    request for strict thinking-mode providers (DeepSeek).
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
