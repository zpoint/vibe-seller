"""Unit tests for catalog staleness check and rotation."""

import time

import pytest

from app.prompts import CATALOG_DESC_L3
from app.workspace.knowledge_sync import KnowledgeSyncManager

pytestmark = pytest.mark.unit


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Set up a fake workspace directory."""
    monkeypatch.setattr(
        'app.workspace.knowledge_sync.VIBE_SELLER_DIR', tmp_path
    )
    knowledge = tmp_path / 'knowledge'
    project = knowledge / 'project'
    project.mkdir(parents=True)
    stores = tmp_path / 'stores'
    stores.mkdir()
    return tmp_path


@pytest.fixture
def mgr():
    return KnowledgeSyncManager()


class TestCatalogNeedsUpdate:
    def test_missing_l2_catalog_is_stale(self, workspace, mgr):
        l2, l3 = mgr.catalog_needs_update()
        assert l2 is True

    def test_l2_up_to_date(self, workspace, mgr):
        knowledge = workspace / 'knowledge'
        (knowledge / 'notes.md').write_text('note')
        time.sleep(0.05)
        (knowledge / 'CATALOG.md').write_text('catalog')
        l2, _ = mgr.catalog_needs_update()
        assert l2 is False

    def test_l2_stale_when_file_newer(self, workspace, mgr):
        knowledge = workspace / 'knowledge'
        (knowledge / 'CATALOG.md').write_text('catalog')
        time.sleep(0.05)
        (knowledge / 'notes.md').write_text('updated')
        l2, _ = mgr.catalog_needs_update()
        assert l2 is True

    def test_l2_includes_project_files(self, workspace, mgr):
        """L2 merges L1 rows, so project/ changes make L2 stale."""
        knowledge = workspace / 'knowledge'
        (knowledge / 'CATALOG.md').write_text('catalog')
        time.sleep(0.05)
        (knowledge / 'project' / 'new.md').write_text('new')
        l2, _ = mgr.catalog_needs_update()
        assert l2 is True

    def test_l2_stale_when_l1_catalog_newer(self, workspace, mgr):
        """L1 CATALOG.md update (e.g. new entry) makes L2 stale."""
        knowledge = workspace / 'knowledge'
        (knowledge / 'CATALOG.md').write_text('catalog')
        time.sleep(0.05)
        (knowledge / 'project' / 'CATALOG.md').write_text('updated')
        l2, _ = mgr.catalog_needs_update()
        assert l2 is True

    def test_missing_l3_catalog_is_stale(self, workspace, mgr):
        store = workspace / 'stores' / 'my-store'
        store.mkdir()
        (store / 'STORE.md').write_text('store')
        _, l3 = mgr.catalog_needs_update('my-store')
        assert l3 is True

    def test_l3_up_to_date(self, workspace, mgr):
        store = workspace / 'stores' / 'my-store'
        store.mkdir()
        (store / 'STORE.md').write_text('store')
        time.sleep(0.05)
        (store / 'CATALOG.md').write_text('catalog')
        _, l3 = mgr.catalog_needs_update('my-store')
        assert l3 is False

    def test_l3_stale_when_file_newer(self, workspace, mgr):
        store = workspace / 'stores' / 'my-store'
        store.mkdir()
        (store / 'CATALOG.md').write_text('catalog')
        time.sleep(0.05)
        (store / 'notes.md').write_text('updated')
        _, l3 = mgr.catalog_needs_update('my-store')
        assert l3 is True

    def test_l3_stale_when_l2_newer(self, workspace, mgr):
        """L3 copies L2 rows, so L2 regen makes L3 stale."""
        knowledge = workspace / 'knowledge'
        store = workspace / 'stores' / 'my-store'
        store.mkdir()
        (store / 'CATALOG.md').write_text('catalog')
        time.sleep(0.05)
        (knowledge / 'CATALOG.md').write_text('updated l2')
        _, l3 = mgr.catalog_needs_update('my-store')
        assert l3 is True

    def test_no_store_returns_false_l3(self, workspace, mgr):
        _, l3 = mgr.catalog_needs_update(None)
        assert l3 is False


class TestRotateCatalogs:
    def test_rotate_deletes_and_saves_content(self, workspace):
        knowledge = workspace / 'knowledge'
        store = workspace / 'stores' / 'my-store'
        store.mkdir()
        (knowledge / 'CATALOG.md').write_text('l2 content')
        (store / 'CATALOG.md').write_text('l3 content')

        saved = KnowledgeSyncManager.rotate_catalogs('my-store')

        assert 'l2' in saved
        assert 'l3' in saved
        # Files removed — agent can't see them
        assert not (knowledge / 'CATALOG.md').exists()
        assert not (store / 'CATALOG.md').exists()
        # Content preserved in memory
        assert saved['l2'][1] == 'l2 content'
        assert saved['l3'][1] == 'l3 content'

    def test_rotate_no_catalog_is_noop(self, workspace):
        saved = KnowledgeSyncManager.rotate_catalogs('no-store')
        assert saved == {}

    def test_rotate_skips_l2_when_not_stale(self, workspace):
        """Only L3 stale → L2 catalog must survive."""
        knowledge = workspace / 'knowledge'
        store = workspace / 'stores' / 'my-store'
        store.mkdir()
        (knowledge / 'CATALOG.md').write_text('l2 content')
        (store / 'CATALOG.md').write_text('l3 content')

        saved = KnowledgeSyncManager.rotate_catalogs(
            'my-store',
            l2_stale=False,
            l3_stale=True,
        )

        assert 'l2' not in saved
        assert 'l3' in saved
        assert (knowledge / 'CATALOG.md').exists()
        assert not (store / 'CATALOG.md').exists()

    def test_rotate_skips_l3_when_not_stale(self, workspace):
        """Only L2 stale → L3 catalog must survive."""
        knowledge = workspace / 'knowledge'
        store = workspace / 'stores' / 'my-store'
        store.mkdir()
        (knowledge / 'CATALOG.md').write_text('l2 content')
        (store / 'CATALOG.md').write_text('l3 content')

        saved = KnowledgeSyncManager.rotate_catalogs(
            'my-store',
            l2_stale=True,
            l3_stale=False,
        )

        assert 'l2' in saved
        assert 'l3' not in saved
        assert not (knowledge / 'CATALOG.md').exists()
        assert (store / 'CATALOG.md').exists()


class TestRestoreCatalogs:
    def test_restore_writes_content_back(self, workspace):
        knowledge = workspace / 'knowledge'
        cat = knowledge / 'CATALOG.md'

        KnowledgeSyncManager.restore_catalogs({'l2': (cat, 'original content')})
        assert cat.read_text() == 'original content'

    def test_restore_skips_if_new_exists(self, workspace):
        """Don't overwrite a catalog the agent wrote."""
        knowledge = workspace / 'knowledge'
        cat = knowledge / 'CATALOG.md'
        cat.write_text('agent wrote this')

        KnowledgeSyncManager.restore_catalogs({'l2': (cat, 'old content')})
        assert cat.read_text() == 'agent wrote this'


class TestCatalogDescContracts:
    """Prompt templates must contain correct placeholders."""

    def test_l3_desc_contains_slug_placeholder(self):
        assert '<slug>' in CATALOG_DESC_L3

    def test_l3_desc_slug_is_replaceable(self):
        rendered = CATALOG_DESC_L3.replace('<slug>', 'my-store')
        assert '<slug>' not in rendered
        assert 'stores/my-store/' in rendered

    def test_l3_desc_classifier_is_filename_only(self):
        """The L3 file-classification rule must be filename-based, not
        content-based.

        Regression guard: e2e test_catalog_sync_and_agent_usage failed
        when the agent excluded `notes.md` from a store's L3 catalog
        because its content "looked minimal". The agent's thinking
        trace explicitly cited the spec phrase
        "procedural, transferable across runs" as a content
        criterion that overrode the filename rule, then dropped the
        file. The fix is to remove every phrase that invites a
        content-based judgment and to require empty/stub files to be
        included with a placeholder summary.
        """
        # Anchor: the rule is filename-only, stated up front.
        assert 'by filename only' in CATALOG_DESC_L3.lower()

        # Explicit INCLUDE pattern listing — agent should not have to
        # infer it from prose.
        assert 'INCLUDE' in CATALOG_DESC_L3
        for example in (
            'notes.md',
            'browser-tips.md',
            'logistics.md',
            'STORE.md',
        ):
            assert example in CATALOG_DESC_L3, (
                f'INCLUDE example missing: {example}'
            )

        # Explicit EXCLUDE pattern listing.
        assert 'EXCLUDE' in CATALOG_DESC_L3
        for example in ('_PLAN_', '_REPORT_', 'YYYY-MM-DD'):
            assert example in CATALOG_DESC_L3, (
                f'EXCLUDE example missing: {example}'
            )

        # Anti-pattern guard: the prompt must NOT say "decide if it's
        # transferable / procedural / actionable" — that's content
        # judgment language. Lowercased to catch any casing.
        body = CATALOG_DESC_L3.lower()
        forbidden = [
            'transferable across runs',
            'is this **l3 knowledge**',
        ]
        for phrase in forbidden:
            assert phrase not in body, (
                f'Content-judgment phrase resurfaced: {phrase!r} — '
                'the L3 classifier must stay filename-only.'
            )

        # Empty/stub files must still be included with a placeholder,
        # not dropped.
        assert 'stub' in body or 'empty' in body
        assert 'accumulates here' in body or 'knowledge accumulates' in body


class TestCleanupBackups:
    def test_cleanup_is_noop(self, workspace):
        """In-memory approach has nothing to clean up."""
        knowledge = workspace / 'knowledge'
        cat = knowledge / 'CATALOG.md'
        # Should not raise
        KnowledgeSyncManager.cleanup_catalog_backups({'l2': (cat, 'content')})
