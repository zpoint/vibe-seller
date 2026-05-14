"""Unit tests for knowledge display in get_structured().

Verifies:
  - __pycache__ entries are filtered out
  - Multi-level paths are preserved (not flattened)
  - project vs local knowledge split works correctly
"""

import pytest

from app.workspace.manager import WorkspaceManager

pytestmark = pytest.mark.unit


def _make_workspace(tmp_path):
    """Create a minimal workspace root with dirs."""
    root = tmp_path / 'ws'
    root.mkdir()
    (root / '.claude' / 'skills').mkdir(parents=True)
    (root / '.git').mkdir()
    (root / 'knowledge' / 'project' / 'common').mkdir(parents=True)
    (root / 'stores').mkdir()
    return root


class TestPycacheFiltering:
    async def test_pycache_excluded_from_project_knowledge(self, tmp_path):
        root = _make_workspace(tmp_path)
        proj = root / 'knowledge' / 'project'

        # Real knowledge file
        (proj / 'common' / 'amazon-sites.md').write_text('# Amazon')

        # __pycache__ junk
        pycache = proj / '__pycache__'
        pycache.mkdir()
        (pycache / 'gen.cpython-313.pyc').write_bytes(b'\x00')

        mgr = WorkspaceManager(root)
        result = await mgr.get_structured()

        names = [f['name'] for f in result['project_knowledge']]
        assert 'amazon-sites.md' in names
        assert 'gen.cpython-313.pyc' not in names

    async def test_pycache_excluded_from_local_knowledge(self, tmp_path):
        root = _make_workspace(tmp_path)
        knowledge = root / 'knowledge'

        (knowledge / 'notes.md').write_text('# Notes')

        pycache = knowledge / '__pycache__'
        pycache.mkdir()
        (pycache / 'cache.pyc').write_bytes(b'\x00')

        mgr = WorkspaceManager(root)
        result = await mgr.get_structured()

        names = [f['name'] for f in result['local_knowledge']]
        assert 'notes.md' in names
        assert 'cache.pyc' not in names


class TestMultiLevelPaths:
    async def test_project_knowledge_preserves_nested_paths(self, tmp_path):
        """Files in subdirs should have full path, not just filename."""
        root = _make_workspace(tmp_path)
        proj = root / 'knowledge' / 'project'

        (proj / 'README.md').write_text('# README')
        (proj / 'common' / 'amazon-sites.md').write_text('# Amazon')
        (proj / 'common' / 'ziniao-browser.md').write_text('# Ziniao')

        mgr = WorkspaceManager(root)
        result = await mgr.get_structured()

        paths = [f['path'] for f in result['project_knowledge']]
        assert 'knowledge/project/README.md' in paths
        assert 'knowledge/project/common/amazon-sites.md' in paths
        assert 'knowledge/project/common/ziniao-browser.md' in paths

    async def test_display_prefix_strips_correctly(self, tmp_path):
        """Verify the displayPrefix='knowledge/project/' contract.

        The frontend uses displayPrefix to strip the path prefix
        and show e.g. 'common/amazon-sites.md' instead of the
        full 'knowledge/project/common/amazon-sites.md'.
        """
        root = _make_workspace(tmp_path)
        proj = root / 'knowledge' / 'project'
        (proj / 'common' / 'amazon-sites.md').write_text('# Amazon')
        (proj / 'CATALOG.md').write_text('# Catalog')

        mgr = WorkspaceManager(root)
        result = await mgr.get_structured()

        prefix = 'knowledge/project/'
        display_names = [
            f['path'].replace(prefix, '') for f in result['project_knowledge']
        ]
        assert 'CATALOG.md' in display_names
        assert 'common/amazon-sites.md' in display_names
        # No bare 'amazon-sites.md' — must include subdir
        bare_names = [f['name'] for f in result['project_knowledge']]
        assert 'amazon-sites.md' in bare_names  # name field is bare
        # But path field is full
        full_paths = [f['path'] for f in result['project_knowledge']]
        assert all(p.startswith(prefix) for p in full_paths)


class TestSkillSymlinkReads:
    """Symlinked custom skills must be viewable in the UI.

    Users install custom skills by symlinking a directory from a private
    repo into ``.claude/skills/<slug>``. The "view file" action must work
    for those — but only for skill reads, so agent-writable dirs stay
    protected against planted-symlink escapes.
    """

    async def test_symlinked_skill_file_readable(self, tmp_path):
        root = _make_workspace(tmp_path)
        external = tmp_path / 'external' / 'tax-filing'
        external.mkdir(parents=True)
        (external / 'SKILL.md').write_text('body')
        (root / '.claude' / 'skills' / 'tax-filing').symlink_to(external)

        mgr = WorkspaceManager(root)
        content = await mgr.read_file('.claude/skills/tax-filing/SKILL.md')
        assert content == 'body'

    async def test_symlink_in_knowledge_still_blocked(self, tmp_path):
        """An attacker-planted symlink under knowledge/ must NOT escape.

        Agents have write access to knowledge/ via MCP and shell access
        via Bash; the strict guard there is the only thing stopping a
        planted symlink from becoming arbitrary host-file read.
        """
        root = _make_workspace(tmp_path)
        secret = tmp_path / 'secret.txt'
        secret.write_text('should-not-be-readable')
        (root / 'knowledge' / 'escape').symlink_to(secret)

        mgr = WorkspaceManager(root)
        with pytest.raises(ValueError, match='traversal'):
            await mgr.read_file('knowledge/escape')

    async def test_skill_read_rejects_dot_dot(self, tmp_path):
        root = _make_workspace(tmp_path)
        mgr = WorkspaceManager(root)
        with pytest.raises(ValueError, match='traversal'):
            await mgr.read_file('.claude/skills/../../etc/passwd')

    async def test_skill_read_rejects_absolute(self, tmp_path):
        root = _make_workspace(tmp_path)
        mgr = WorkspaceManager(root)
        with pytest.raises(ValueError, match='traversal'):
            # Even though prefix looks like a skill, absolute path is rejected.
            await mgr.read_file('/etc/passwd')


class TestSkillListing:
    async def test_pycache_excluded_from_skill_files(self, tmp_path):
        """__pycache__ and .pyc files must not appear in a skill's file list."""
        root = _make_workspace(tmp_path)
        skill = root / '.claude' / 'skills' / 'tax-filing'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text('---\nname: tax-filing\n---\n')
        (skill / 'cli.py').write_text('print("hi")')
        cache = skill / '__pycache__'
        cache.mkdir()
        (cache / 'cli.cpython-313.pyc').write_bytes(b'\x00')
        (cache / 'sanitize.cpython-313.pyc').write_bytes(b'\x00')

        mgr = WorkspaceManager(root)
        result = await mgr.get_structured()

        skill_entry = next(
            s for s in result['skills'] if s['slug'] == 'tax-filing'
        )
        names = [f['name'] for f in skill_entry['files']]
        assert 'SKILL.md' in names
        assert 'cli.py' in names
        assert not any(n.endswith('.pyc') for n in names), names
        paths = [f['path'] for f in skill_entry['files']]
        assert not any('__pycache__' in p.split('/') for p in paths), paths


class TestKnowledgeSplit:
    async def test_project_vs_local_split(self, tmp_path):
        """Files under project/ go to project_knowledge,
        others to local_knowledge."""
        root = _make_workspace(tmp_path)

        (root / 'knowledge' / 'notes.md').write_text('# Local')
        (root / 'knowledge' / 'project' / 'README.md').write_text('# Project')

        mgr = WorkspaceManager(root)
        result = await mgr.get_structured()

        proj_names = [f['name'] for f in result['project_knowledge']]
        local_names = [f['name'] for f in result['local_knowledge']]

        assert 'README.md' in proj_names
        assert 'notes.md' in local_names
        assert 'README.md' not in local_names
        assert 'notes.md' not in proj_names
