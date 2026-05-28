"""Tests for ``WorkspaceManager.create_store_profile``.

The single load-bearing assertion: a freshly-scaffolded store has an
L3 ``CATALOG.md`` on disk from turn 1, so the agent's catalog-first
contract is satisfiable without depending on the scheduled L3 sync
having already run. See ``app/ai/bash_safety.py``
``should_mark_catalog_read`` for the matching read-side contract.
"""

from pathlib import Path

import git as gitlib
import pytest

from app.workspace.manager import WorkspaceManager

pytestmark = pytest.mark.unit


@pytest.fixture
def ws(tmp_path: Path) -> WorkspaceManager:
    mgr = WorkspaceManager(root=tmp_path)
    repo = gitlib.Repo.init(str(tmp_path))
    repo.config_writer().set_value('user', 'name', 'Test').release()
    repo.config_writer().set_value('user', 'email', 'test@test').release()
    (tmp_path / '.gitignore').write_text('*.pyc\n')
    repo.index.add(['.gitignore'])
    repo.index.commit('Initial commit')
    return mgr


async def test_creates_catalog_md_stub(ws: WorkspaceManager, tmp_path: Path):
    """The whole point of the fix — a fresh store has a readable
    L3 catalog from turn 1, even before any L3 catalog-sync job
    has run."""
    await ws.create_store_profile(
        slug='fresh-store', name='Fresh Store', backend='chrome'
    )
    catalog = tmp_path / 'stores' / 'fresh-store' / 'CATALOG.md'
    assert catalog.is_file(), (
        "Fresh store must seed an L3 CATALOG.md so the agent's "
        'catalog-first Read on turn 1 succeeds.'
    )
    content = catalog.read_text()
    # Catalog-shaped Markdown so the hook's path predicate matches
    # AND the agent gets something useful when it Reads.
    assert '| File' in content
    assert 'stores/fresh-store/STORE.md' in content


async def test_catalog_md_stub_co_exists_with_scaffold(
    ws: WorkspaceManager, tmp_path: Path
):
    """Existing scaffold files (STORE.md / notes.md / logistics.md)
    must still be created — the catalog stub addition is purely
    additive."""
    await ws.create_store_profile(
        slug='scaffold-check', name='Scaffold Check', backend='chrome'
    )
    store_dir = tmp_path / 'stores' / 'scaffold-check'
    for fname in ('STORE.md', 'notes.md', 'logistics.md', 'CATALOG.md'):
        assert (store_dir / fname).is_file(), f'{fname} missing'


async def test_catalog_md_stub_not_overwritten_on_recreate(
    ws: WorkspaceManager, tmp_path: Path
):
    """If the L3 catalog-sync job has already populated CATALOG.md
    with real content, calling ``create_store_profile`` again
    (idempotent recreate path) must NOT clobber it back to the
    stub."""
    catalog = tmp_path / 'stores' / 'persistent' / 'CATALOG.md'
    catalog.parent.mkdir(parents=True)
    real_content = '# Real Catalog\n\n| File | Relevance | Summary |\n'
    catalog.write_text(real_content)

    await ws.create_store_profile(
        slug='persistent', name='Persistent', backend='chrome'
    )
    assert catalog.read_text() == real_content
