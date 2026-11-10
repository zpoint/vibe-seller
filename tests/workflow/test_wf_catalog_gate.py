"""Workflow: the catalog gate is enforced on the real write path.

``PUT /api/workspace/file`` is the surface every catalog writer uses —
the sync agent's ``vibe_seller_write_workspace_file`` MCP tool calls
exactly this route. These tests build a workspace on disk (through the
``mock_workspace`` root), then drive valid and invalid CATALOG.md
writes through the HTTP endpoint: valid tiers land, and a catalog that
drops a row or leaks another store's files is a 400 whose detail names
the fix — that detail is the redo instruction the sync agent sees as
its tool error.
"""

import pytest

pytestmark = pytest.mark.workflow

HEADER = '| File | Relevance | Summary |\n|---|---|---|\n'

L1 = HEADER + '| common/sites-amazon.md | amazon | Seller Central URLs |\n'

L2 = (
    HEADER + '| knowledge/project/common/sites-amazon.md | amazon | '
    'Seller Central URLs |\n'
    '| knowledge/notes.md | amazon | Shared pricing notes |\n'
)

L3 = (
    L2 + '| stores/gate-store/STORE.md | | Store profile |\n'
    '| stores/gate-store/notes.md | | Store-specific notes |\n'
)


def _seed(root):
    """A workspace whose L1 ships one row and one store exists."""
    project = root / 'knowledge' / 'project' / 'common'
    project.mkdir(parents=True)
    (root / 'knowledge' / 'project' / 'CATALOG.md').write_text(L1)
    (root / 'knowledge' / 'notes.md').write_text(
        '# Notes\n\nShared pricing notes for widgets across stores.\n'
    )
    store = root / 'stores' / 'gate-store'
    store.mkdir(parents=True)
    (store / 'STORE.md').write_text('# Store\n\nThe gate-store profile.\n')
    (store / 'notes.md').write_text(
        '# Notes\n\nGate-store specific pricing behaviour.\n'
    )
    (store / 'metadata.json').write_text('{"platforms": {"amazon": ["US"]}}')
    other = root / 'stores' / 'other-store'
    other.mkdir(parents=True)
    (other / 'notes.md').write_text('# Notes\n\nOther store.\n')


class TestCatalogGateOnWritePath:
    async def test_valid_l2_lands(self, admin_client, mock_workspace):
        _seed(mock_workspace.root)
        r = await admin_client.put(
            '/api/workspace/file?path=knowledge/CATALOG.md',
            json={'content': L2},
        )
        assert r.status_code == 200, r.text
        assert (
            mock_workspace.root / 'knowledge' / 'CATALOG.md'
        ).read_text() == L2

    async def test_l2_dropping_l1_row_is_400_naming_it(
        self, admin_client, mock_workspace
    ):
        _seed(mock_workspace.root)
        broken = L2.replace(
            '| knowledge/project/common/sites-amazon.md | amazon | '
            'Seller Central URLs |\n',
            '',
        )
        r = await admin_client.put(
            '/api/workspace/file?path=knowledge/CATALOG.md',
            json={'content': broken},
        )
        assert r.status_code == 400, r.text
        assert 'knowledge/project/common/sites-amazon.md' in r.json()['detail']
        # rejected write must not land
        assert not (mock_workspace.root / 'knowledge' / 'CATALOG.md').exists()

    async def test_valid_l3_lands_with_standing_l2(
        self, admin_client, mock_workspace
    ):
        _seed(mock_workspace.root)
        (mock_workspace.root / 'knowledge' / 'CATALOG.md').write_text(L2)
        r = await admin_client.put(
            '/api/workspace/file?path=stores/gate-store/CATALOG.md',
            json={'content': L3},
        )
        assert r.status_code == 200, r.text

    async def test_l3_missing_l2_row_is_400_naming_it(
        self, admin_client, mock_workspace
    ):
        _seed(mock_workspace.root)
        (mock_workspace.root / 'knowledge' / 'CATALOG.md').write_text(L2)
        broken = L3.replace(
            '| knowledge/notes.md | amazon | Shared pricing notes |\n', ''
        )
        r = await admin_client.put(
            '/api/workspace/file?path=stores/gate-store/CATALOG.md',
            json={'content': broken},
        )
        assert r.status_code == 400, r.text
        assert 'missing L2 row: knowledge/notes.md' in r.json()['detail']

    async def test_l3_with_other_store_row_is_400(
        self, admin_client, mock_workspace
    ):
        _seed(mock_workspace.root)
        (mock_workspace.root / 'knowledge' / 'CATALOG.md').write_text(L2)
        leak = L3 + '| stores/other-store/notes.md | | leaked |\n'
        r = await admin_client.put(
            '/api/workspace/file?path=stores/gate-store/CATALOG.md',
            json={'content': leak},
        )
        assert r.status_code == 400, r.text
        assert 'stores/other-store/notes.md' in r.json()['detail']

    async def test_non_catalog_writes_untouched(
        self, admin_client, mock_workspace
    ):
        """The gate must not tax ordinary knowledge writes."""
        _seed(mock_workspace.root)
        r = await admin_client.put(
            '/api/workspace/file?path=knowledge/plain.md',
            json={'content': '# Plain\n\nAny content at all.\n'},
        )
        assert r.status_code == 200, r.text
