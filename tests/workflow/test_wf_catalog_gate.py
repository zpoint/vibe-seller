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

from app.ai.stop_gates import _attempts  # noqa: PLC2701 — episode state

pytestmark = pytest.mark.workflow


@pytest.fixture(autouse=True)
def _clear_attempts():
    """Every test starts its own episode of the content rule."""
    _attempts.clear()
    yield
    _attempts.clear()


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

    async def test_a_content_rule_is_told_once(
        self, admin_client, mock_workspace
    ):
        """A catalog missing a row is refused once, naming the row; the
        same write a second time lands as written."""
        _seed(mock_workspace.root)
        (mock_workspace.root / 'knowledge' / 'CATALOG.md').write_text(L2)
        partial = L3.replace(
            '| knowledge/notes.md | amazon | Shared pricing notes |\n', ''
        )
        url = '/api/workspace/file?path=stores/gate-store/CATALOG.md'
        r = await admin_client.put(url, json={'content': partial})
        assert r.status_code == 400, r.text
        r = await admin_client.put(url, json={'content': partial})
        assert r.status_code == 200, r.text

    async def test_a_content_rule_is_told_once_per_episode(
        self, admin_client, mock_workspace
    ):
        """The same missing row is told once, however often it is
        rewritten: 400, 200, 200. A write the rules have nothing to say
        about closes that episode, so the next broken one is refused
        again."""
        _seed(mock_workspace.root)
        (mock_workspace.root / 'knowledge' / 'CATALOG.md').write_text(L2)
        partial = L3.replace(
            '| knowledge/notes.md | amazon | Shared pricing notes |\n', ''
        )
        url = '/api/workspace/file?path=stores/gate-store/CATALOG.md'
        r = await admin_client.put(url, json={'content': partial})
        assert r.status_code == 400, r.text
        for _ in range(2):
            r = await admin_client.put(url, json={'content': partial})
            assert r.status_code == 200, r.text
        r = await admin_client.put(url, json={'content': L3})
        assert r.status_code == 200, r.text
        r = await admin_client.put(url, json={'content': partial})
        assert r.status_code == 400, r.text

    async def test_another_stores_row_is_always_refused(
        self, admin_client, mock_workspace
    ):
        _seed(mock_workspace.root)
        (mock_workspace.root / 'knowledge' / 'CATALOG.md').write_text(L2)
        leak = L3 + '| stores/other-store/notes.md | | leaked |\n'
        url = '/api/workspace/file?path=stores/gate-store/CATALOG.md'
        for _ in range(2):
            r = await admin_client.put(url, json={'content': leak})
            assert r.status_code == 400, r.text

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
