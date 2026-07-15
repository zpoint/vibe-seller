"""Unit tests for the save-skill flow.

Covers the WorkspaceManager upsert/list logic (including the HARD
INVARIANT that built-in slugs are read-only) and the two MCP tool
dispatch branches (vibe_seller_list_skills / vibe_seller_save_skill).
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.mcp_server import handle_tool_call
from app.workspace.manager import WorkspaceManager

pytestmark = pytest.mark.unit


def _mgr(tmp_path, synced=None):
    """A WorkspaceManager on a bare tmp workspace.

    If `synced` is given, write a .sync_meta.json marking those slugs
    as built-in (maintainer-synced, read-only).
    """
    root = tmp_path / 'ws'
    skills_dir = root / '.claude' / 'skills'
    skills_dir.mkdir(parents=True)
    (root / '.git').mkdir()
    if synced:
        (skills_dir / '.sync_meta.json').write_text(
            json.dumps({'synced_skills': synced})
        )
    return WorkspaceManager(root), skills_dir


SKILL_MD = (
    '---\nname: Revenue Report\n'
    'description: "Export orders and sum revenue. Use when asked to '
    'total revenue from an export."\n---\n\n# Revenue Report\n\nBody.\n'
)


class TestSaveSkillCreate:
    async def test_creates_user_space_skill(self, tmp_path):
        mgr, skills_dir = _mgr(tmp_path)
        result = await mgr.save_skill('revenue-report', SKILL_MD)

        assert result['action'] == 'created'
        assert result['slug'] == 'revenue-report'
        skill_md = skills_dir / 'revenue-report' / 'SKILL.md'
        assert skill_md.read_text() == SKILL_MD

        lock = mgr._read_lockfile()['skills']['revenue-report']
        assert lock['source'] == 'local'  # user-space, editable
        assert lock['name'] == 'Revenue Report'  # from frontmatter
        assert lock['created_at'] and lock['updated_at']

    async def test_writes_bundled_files(self, tmp_path):
        mgr, skills_dir = _mgr(tmp_path)
        await mgr.save_skill(
            'revenue-report',
            SKILL_MD,
            files={'references/notes.md': '# notes'},
        )
        notes = skills_dir / 'revenue-report' / 'references' / 'notes.md'
        assert notes.read_text() == '# notes'


class TestSaveSkillExtend:
    async def test_overwrite_preserves_created_at(self, tmp_path):
        mgr, skills_dir = _mgr(tmp_path)
        first = await mgr.save_skill('revenue-report', SKILL_MD)
        created = mgr._read_lockfile()['skills']['revenue-report']['created_at']
        assert first['action'] == 'created'

        extended = SKILL_MD + '\n## Also: send to WeCom\n'
        second = await mgr.save_skill('revenue-report', extended)

        assert second['action'] == 'updated'
        assert (skills_dir / 'revenue-report' / 'SKILL.md').read_text() == (
            extended
        )
        # created_at is preserved; only one directory exists (no dupe).
        lock = mgr._read_lockfile()['skills']['revenue-report']
        assert lock['created_at'] == created
        dirs = [p.name for p in skills_dir.iterdir() if p.is_dir()]
        assert dirs == ['revenue-report']

    async def test_imported_stays_imported(self, tmp_path):
        """Overwriting an imported (source=url) skill keeps it imported."""
        mgr, skills_dir = _mgr(tmp_path)
        lock = mgr._read_lockfile()
        lock['skills']['remote'] = {
            'source': 'url',
            'name': 'remote',
            'origin_url': 'https://example.com/r',
        }
        mgr._write_lockfile(lock)
        (skills_dir / 'remote').mkdir()

        await mgr.save_skill('remote', SKILL_MD)
        entry = mgr._read_lockfile()['skills']['remote']
        assert entry['source'] == 'url'  # not downgraded to local


class TestSaveSkillRejectsBuiltin:
    """HARD INVARIANT: built-in slugs are maintainer-owned, read-only."""

    async def test_builtin_slug_rejected(self, tmp_path):
        mgr, skills_dir = _mgr(tmp_path, synced=['amazon-listing'])
        # A physical builtin dir exists (as after a sync).
        (skills_dir / 'amazon-listing').mkdir()
        (skills_dir / 'amazon-listing' / 'SKILL.md').write_text('# builtin')

        with pytest.raises(ValueError, match='built-in'):
            await mgr.save_skill('amazon-listing', SKILL_MD)

        # The builtin content is untouched.
        assert (skills_dir / 'amazon-listing' / 'SKILL.md').read_text() == (
            '# builtin'
        )


class TestSaveSkillValidation:
    async def test_invalid_slug_rejected(self, tmp_path):
        mgr, _ = _mgr(tmp_path)
        for bad in ['Revenue Report', '_builtin', 'a/b', '', 'UP']:
            with pytest.raises(ValueError, match='slug'):
                await mgr.save_skill(bad, SKILL_MD)

    async def test_bad_file_path_rejected(self, tmp_path):
        mgr, _ = _mgr(tmp_path)
        for bad in ['../escape.md', '/abs.md', 'SKILL.md', 'a/../../x']:
            with pytest.raises(ValueError, match='file path'):
                await mgr.save_skill('ok-slug', SKILL_MD, files={bad: 'x'})


class TestListSkills:
    async def test_shape_and_updatable(self, tmp_path):
        mgr, skills_dir = _mgr(tmp_path, synced=['builtin-one'])
        # builtin (synced), custom (saved), imported (lockfile url)
        (skills_dir / 'builtin-one').mkdir()
        (skills_dir / 'builtin-one' / 'SKILL.md').write_text(
            '---\nname: B\ndescription: builtin\n---\n'
        )
        await mgr.save_skill('my-custom', SKILL_MD)
        lock = mgr._read_lockfile()
        lock['skills']['imp'] = {
            'source': 'url',
            'name': 'imp',
            'origin_url': 'https://example.com',
        }
        mgr._write_lockfile(lock)
        (skills_dir / 'imp').mkdir()
        (skills_dir / 'imp' / 'SKILL.md').write_text(
            '---\nname: Imp\ndescription: imported\n---\n'
        )

        skills = {s['slug']: s for s in await mgr.list_skills()}

        assert skills['builtin-one']['source'] == 'builtin'
        assert skills['builtin-one']['updatable'] is False
        assert skills['my-custom']['source'] == 'custom'
        assert skills['my-custom']['updatable'] is True
        assert skills['my-custom']['name'] == 'Revenue Report'
        assert skills['imp']['source'] == 'imported'
        assert skills['imp']['updatable'] is True
        # Every entry has the full contract shape.
        for s in skills.values():
            assert set(s) == {
                'slug',
                'name',
                'description',
                'source',
                'updatable',
            }


class TestMcpDispatch:
    """The two new MCP tools call the right endpoints."""

    async def test_list_skills_dispatch(self):
        with patch(
            'app.mcp_server.call_api',
            new_callable=AsyncMock,
            return_value=[{'slug': 'x', 'updatable': True}],
        ) as mock_api:
            result = await handle_tool_call('vibe_seller_list_skills', {})
            mock_api.assert_awaited_once_with('GET', '/api/workspace/skills')
            assert json.loads(result)[0]['slug'] == 'x'

    async def test_save_skill_dispatch(self):
        with patch(
            'app.mcp_server.call_api',
            new_callable=AsyncMock,
            return_value={'slug': 'revenue-report', 'action': 'created'},
        ) as mock_api:
            result = await handle_tool_call(
                'vibe_seller_save_skill',
                {
                    'slug': 'revenue-report',
                    'skill_md': SKILL_MD,
                    'files': {'references/n.md': '# n'},
                },
            )
            mock_api.assert_awaited_once_with(
                'PUT',
                '/api/workspace/skills/revenue-report',
                {'skill_md': SKILL_MD, 'files': {'references/n.md': '# n'}},
            )
            assert json.loads(result)['action'] == 'created'

    async def test_save_skill_dispatch_no_files(self):
        with patch(
            'app.mcp_server.call_api',
            new_callable=AsyncMock,
            return_value={'slug': 's', 'action': 'created'},
        ) as mock_api:
            await handle_tool_call(
                'vibe_seller_save_skill',
                {'slug': 's', 'skill_md': SKILL_MD},
            )
            # No `files` key when none supplied.
            mock_api.assert_awaited_once_with(
                'PUT', '/api/workspace/skills/s', {'skill_md': SKILL_MD}
            )
