"""Workspace confinement: real symlinks, real sibling dirs, real caps.

Covers three guarantees of the workspace layer:

* S1 — ``_safe_path`` containment is ``is_relative_to`` on the
  resolved pair, so a prefix-sharing SIBLING (``<root>-evil``) or a
  symlink planted under knowledge/ cannot pass; the skills branch of
  ``resolve_file`` follows maintainer symlinks for the UI but stays
  contained for API-token callers;
* C2 — a store-profile slug outside the derived ``[a-z0-9-]`` shape
  is refused at every layer that turns it into a path (validator,
  manager, REST route, MCP tool);
* M2 — ``workspace_file(read)`` never materialises an oversize file:
  it stats first, previews text from a bounded prefix, and answers a
  binary with the download shape.
"""

from pathlib import Path

import pytest
from starlette.requests import Request

from app.agent_mcp.support import MAX_FILE_CHARS, McpContext
from app.agent_mcp.tools_workspace_files import workspace_file
from app.browser.wrapper import validate_store_slug
from app.models.user import User
from app.workspace.actor import (
    actor_for,
    clear_request_actor,
    set_request_actor,
)
from app.workspace.manager import WorkspaceManager, workspace_manager

pytestmark = pytest.mark.unit


@pytest.fixture
def manager(tmp_path: Path) -> WorkspaceManager:
    """A manager over a scratch workspace root with the real tree."""
    root = tmp_path / 'home' / '.vibe-seller'
    for sub in ('knowledge', 'stores', Path('.claude') / 'skills'):
        (root / sub).mkdir(parents=True)
    return WorkspaceManager(root=root)


@pytest.fixture
def api_token_actor():
    """Run the test as an API-token caller (the actor contextvar
    ``get_current_user`` sets for ``vst_`` credentials)."""
    set_request_actor(actor_for(User(username='external-agent'), 'api_token'))
    yield
    clear_request_actor()


class TestSafePathContainment:
    def test_a_prefix_sharing_sibling_does_not_pass(self, manager, tmp_path):
        """THE S1 regression: ``str(resolved).startswith(str(root))``
        accepted ``<root>-evil`` because it shares the prefix."""
        evil = tmp_path / 'home' / '.vibe-seller-evil'
        evil.mkdir()
        (evil / 'loot.md').write_text('stolen')
        link = manager.root / 'knowledge' / 'esc'
        link.symlink_to(evil, target_is_directory=True)
        with pytest.raises(ValueError, match='traversal'):
            manager._safe_path('knowledge/esc/loot.md')

    def test_a_symlink_to_anywhere_outside_is_refused(self, manager, tmp_path):
        outside = tmp_path / 'outside'
        outside.mkdir()
        (outside / 'x.md').write_text('x')
        (manager.root / 'knowledge' / 'esc').symlink_to(
            outside, target_is_directory=True
        )
        with pytest.raises(ValueError):
            manager._safe_path('knowledge/esc/x.md')

    def test_a_real_file_under_the_root_passes(self, manager):
        target = manager.root / 'knowledge' / 'note.md'
        target.write_text('hello')
        assert manager._safe_path('knowledge/note.md') == target.resolve()


class TestSkillsSymlinkPolicy:
    @pytest.fixture
    def skills_link(self, manager, tmp_path):
        """A maintainer-style external skill: a symlink in the skills
        tree pointing OUTSIDE the workspace, with a readable file.
        Returns the workspace-relative path an agent would ask for."""
        external = tmp_path / 'external-skills' / 'my-skill'
        external.mkdir(parents=True)
        (external / 'SKILL.md').write_text('# external skill')
        link = manager.root / '.claude' / 'skills' / 'my-skill'
        link.symlink_to(external, target_is_directory=True)
        return '.claude/skills/my-skill/SKILL.md'

    def test_the_ui_session_viewer_follows_the_symlink(
        self, manager, skills_link
    ):
        """The supported feature: with no actor on the stack (the UI /
        background shape — session callers get an actor with
        via='session', which is not the api_token class), a
        hand-placed external skill stays readable."""
        path = manager.resolve_file(skills_link)
        assert path.read_text(encoding='utf-8') == '# external skill'

    def test_a_background_caller_follows_the_symlink(
        self, manager, skills_link
    ):
        """Task pipelines clear the actor — the skills tree is also
        read from non-request contexts, and those keep the feature."""
        clear_request_actor()
        assert manager.resolve_file(skills_link).is_file()

    def test_an_api_token_caller_is_contained(
        self, manager, skills_link, api_token_actor
    ):
        """A task agent's bash can plant a symlink in the skills tree;
        an external agent's token must not read the host through it."""
        with pytest.raises(ValueError, match='traversal'):
            manager.resolve_file(skills_link)

    def test_an_api_token_caller_reads_a_real_skill(
        self, manager, api_token_actor
    ):
        real = manager.root / '.claude' / 'skills' / 'builtin-skill'
        real.mkdir()
        (real / 'SKILL.md').write_text('# inside')
        path = manager.resolve_file('.claude/skills/builtin-skill/SKILL.md')
        assert path.read_text(encoding='utf-8') == '# inside'

    def test_an_api_token_caller_cannot_dotdot_either(
        self, manager, api_token_actor
    ):
        with pytest.raises(ValueError):
            manager.resolve_file('.claude/skills/../../knowledge/note.md')


class TestStoreSlugValidation:
    @pytest.mark.parametrize(
        'slug', ['/tmp/evil', '../../..', 'acme/notes.md', 'Acme', 'acme_1',
                 '-acme', 'acme-', 'a--b', '', 'acme.md']
    )
    def test_out_of_shape_slugs_are_refused(self, slug):
        with pytest.raises(ValueError):
            validate_store_slug(slug)

    @pytest.mark.parametrize(
        'slug', ['acme', 'acme-shop', 'store-2', 'a', '123']
    )
    def test_derived_shapes_pass(self, slug):
        assert validate_store_slug(slug) == slug

    async def test_the_manager_refuses_an_escaping_slug(
        self, manager, tmp_path
    ):
        """The layer that builds the path validates too — a caller
        above the route (or a future route) cannot skip it."""
        before = set(tmp_path.rglob('*'))
        with pytest.raises(ValueError):
            await manager.create_store_profile(slug='/tmp/evil', name='acme')
        assert set(tmp_path.rglob('*')) == before

    async def test_the_manager_scaffolds_a_valid_slug(self, manager):
        rel = await manager.create_store_profile(slug='acme', name='Acme Ltd')
        assert rel == 'stores/acme'
        assert (manager.root / 'stores' / 'acme' / 'STORE.md').exists()


# ── M2: the read tool stats before it reads ──────────────────────
#
# The tool layer runs against the real (test-isolated) workspace
# singleton the routes use; files are planted with plain pathlib.


def _ctx() -> McpContext:
    scope = {
        'type': 'http',
        'method': 'POST',
        'path': '/api/mcp',
        'headers': [(b'host', b'testserver')],
        'query_string': b'',
    }
    return McpContext(
        user=User(username='ci'), db=None, request=Request(scope)
    )


async def _read(path: str) -> dict:
    return await workspace_file(_ctx(), {'action': 'read', 'path': path})


@pytest.fixture
def ws_root():
    root = workspace_manager.root
    (root / 'knowledge').mkdir(parents=True, exist_ok=True)
    yield root


class TestReadStatsFirst:
    async def test_a_small_text_file_is_returned_inline(self, ws_root):
        target = ws_root / 'knowledge' / 'small.md'
        target.write_text('# small note')
        result = await _read('knowledge/small.md')
        assert result['content'] == '# small note'
        assert result['truncated'] is False

    async def test_an_oversize_text_file_is_previewed_not_materialised(
        self, ws_root
    ):
        """Above the inline cap the tool must NOT read the whole file:
        the preview comes from a bounded prefix and is IDENTICAL to
        what read-then-trim produced for the same file."""
        text = ('line of notes\n' * 40_000)[:400_000]
        assert len(text) > 256 * 1024
        target = ws_root / 'knowledge' / 'big.md'
        target.write_text(text)
        result = await _read('knowledge/big.md')
        assert result['content'].startswith(text[:MAX_FILE_CHARS])
        assert result['truncated'] is True
        assert f'{len(text):,} bytes' in result['content']

    async def test_an_oversize_binary_file_answers_the_url_shape(
        self, ws_root
    ):
        blob = b'\x89PNG\r\n\x1a\n' + bytes(range(256)) * 2_048
        assert len(blob) > 256 * 1024
        target = ws_root / 'knowledge' / 'big.png'
        target.write_bytes(blob)
        result = await _read('knowledge/big.png')
        assert result['encoding'] == 'url'
        assert '/api/workspace/file/raw' in result['url']
        assert 'content' not in result
