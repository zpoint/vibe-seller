"""Unit tests for skills in get_structured() and sync config preservation."""

import json

import pytest

from app.workspace.manager import WorkspaceManager
from app.workspace.skills_sync import SkillsSyncManager

pytestmark = pytest.mark.unit


class TestGetStructuredSkills:
    """Test that get_structured() correctly handles builtin + user skills."""

    async def test_builtin_and_user_skills(self, tmp_path, monkeypatch):
        """Synced skills get source='builtin', others get 'custom'."""
        root = tmp_path / 'ws'
        root.mkdir()
        skills_dir = root / '.claude' / 'skills'

        # Create builtin skill (flat, direct child of skills/)
        builtin = skills_dir / 'skill-a'
        builtin.mkdir(parents=True)
        (builtin / 'SKILL.md').write_text(
            '---\ndescription: A built-in skill\n---\n# A'
        )

        # Create user skill
        user_skill = skills_dir / 'my-skill'
        user_skill.mkdir(parents=True)
        (user_skill / 'SKILL.md').write_text(
            '---\ndescription: My custom skill\n---\n# B'
        )

        # Write sync_meta marking skill-a as synced (builtin)
        (skills_dir / '.sync_meta.json').write_text(
            json.dumps({'synced_skills': ['skill-a']})
        )

        # Ensure other dirs exist
        (root / 'knowledge').mkdir(parents=True, exist_ok=True)
        (root / 'stores').mkdir(parents=True, exist_ok=True)
        (root / '.git').mkdir(parents=True, exist_ok=True)

        mgr = WorkspaceManager(root)
        result = await mgr.get_structured()

        assert len(result['skills']) == 2
        builtin_skill = result['skills'][0]
        user_skill_res = result['skills'][1]

        assert builtin_skill['slug'] == 'my-skill'
        assert builtin_skill['source'] == 'custom'

        assert user_skill_res['slug'] == 'skill-a'
        assert user_skill_res['source'] == 'builtin'
        assert user_skill_res['description'] == 'A built-in skill'

    async def test_venv_filtered_from_skill_files(self, tmp_path):
        """.venv/ directories inside skills should not appear in files."""
        root = tmp_path / 'ws'
        root.mkdir()
        skills_dir = root / '.claude' / 'skills'

        skill = skills_dir / 'test-skill'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text('# Test')
        (skill / 'script.py').write_text('print("hi")')

        # Create .venv dir with some files
        venv = skill / '.venv' / 'lib' / 'python3.11'
        venv.mkdir(parents=True)
        (venv / 'site.py').write_text('# internal')

        (root / 'knowledge').mkdir(parents=True, exist_ok=True)
        (root / 'stores').mkdir(parents=True, exist_ok=True)
        (root / '.git').mkdir(parents=True, exist_ok=True)

        mgr = WorkspaceManager(root)
        result = await mgr.get_structured()

        assert len(result['skills']) == 1
        skill_data = result['skills'][0]
        file_names = [f['name'] for f in skill_data['files']]
        assert 'SKILL.md' in file_names
        assert 'script.py' in file_names
        assert 'site.py' not in file_names
        assert skill_data['file_count'] == 2


class TestSkillsSyncOverwritesAllFiles:
    """Sync overwrites all files — config.md is no longer preserved."""

    async def test_fetch_overwrites_config_md(self, tmp_path, monkeypatch):
        """config.md at dest IS overwritten (no longer preserved)."""
        # Create a fake package source
        src = tmp_path / 'src' / 'skills' / 'test-skill'
        src.mkdir(parents=True)
        (src / 'SKILL.md').write_text('# Updated skill')
        (src / 'config.md').write_text('# Package default config')
        (src / 'script.py').write_text('print("new")')

        # Create dest with existing config.md
        dest = tmp_path / 'dest'
        skill_dest = dest / 'test-skill'
        skill_dest.mkdir(parents=True)
        (skill_dest / 'config.md').write_text('# MY CUSTOM CONFIG')
        (skill_dest / 'SKILL.md').write_text('# Old skill')

        mgr = SkillsSyncManager()
        mgr._dest_dir = dest

        # Monkeypatch the source lookup
        monkeypatch.setattr(
            mgr,
            '_get_local_source',
            lambda: tmp_path / 'src' / 'skills',
        )

        result = await mgr.fetch()
        assert result['synced'] is True

        # config.md is no longer preserved by sync — it gets
        # overwritten if the source has a file with that name.
        # Skills no longer use config.md (seller info goes to stores/).

        # SKILL.md should be updated
        assert (skill_dest / 'SKILL.md').read_text() == '# Updated skill'

        # script.py should be copied
        assert (skill_dest / 'script.py').read_text() == 'print("new")'


class TestSkillsSyncNoConfigAutoInit:
    """Config.md auto-init was removed — skills use stores/ for config."""

    async def test_fetch_does_not_auto_create_config(
        self, tmp_path, monkeypatch
    ):
        """fetch() syncs skill files from the local source directory;
        config.md is not auto-created (skills use stores/ for seller info)."""
        src = tmp_path / 'src' / 'skills' / 'test-skill'
        src.mkdir(parents=True)
        (src / 'SKILL.md').write_text('# Skill')
        (src / 'script.py').write_text('print("hi")')

        dest = tmp_path / 'dest'
        dest.mkdir(parents=True)

        mgr = SkillsSyncManager()
        mgr._dest_dir = dest
        monkeypatch.setattr(
            mgr,
            '_get_local_source',
            lambda: tmp_path / 'src' / 'skills',
        )

        await mgr.fetch()

        skill_dest = dest / 'test-skill'
        assert (skill_dest / 'SKILL.md').exists()
        assert (skill_dest / 'script.py').exists()
        # No config.md should be auto-created
        assert not (skill_dest / 'config.md').exists()


class TestCreateSkillReservedSlug:
    """Test that create_skill rejects reserved _ prefix slugs."""

    async def test_underscore_prefix_raises(self, tmp_path):
        root = tmp_path / 'ws'
        root.mkdir()
        (root / '.claude' / 'skills').mkdir(parents=True)
        (root / '.git').mkdir()

        mgr = WorkspaceManager(root)
        with pytest.raises(ValueError, match='reserved'):
            await mgr.create_skill('_builtin')


class TestLockfile:
    """Test lockfile read/write helpers."""

    async def test_lockfile_round_trip(self, tmp_path):
        root = tmp_path / 'ws'
        root.mkdir()
        (root / '.claude' / 'skills').mkdir(parents=True)
        (root / '.git').mkdir()
        mgr = WorkspaceManager(root)

        data = {
            'version': 1,
            'skills': {
                'test-skill': {
                    'source': 'local',
                    'name': 'test-skill',
                    'created_at': '2026-01-01T00:00:00',
                    'updated_at': '2026-01-01T00:00:00',
                },
            },
        }
        mgr._write_lockfile(data)
        result = mgr._read_lockfile()
        assert result == data

    async def test_lockfile_missing_returns_default(self, tmp_path):
        root = tmp_path / 'ws'
        root.mkdir()
        (root / '.git').mkdir()
        mgr = WorkspaceManager(root)
        result = mgr._read_lockfile()
        assert result == {'version': 1, 'skills': {}}

    async def test_lockfile_corrupt_returns_default(self, tmp_path):
        root = tmp_path / 'ws'
        root.mkdir()
        skills_dir = root / '.claude' / 'skills'
        skills_dir.mkdir(parents=True)
        (skills_dir / 'skills.lock.json').write_text('NOT JSON!')
        (root / '.git').mkdir()
        mgr = WorkspaceManager(root)
        result = mgr._read_lockfile()
        assert result == {'version': 1, 'skills': {}}


class TestGetStructuredImported:
    """Test that lockfile source='url' maps to source='imported'."""

    async def test_imported_skill_from_lockfile(self, tmp_path):
        root = tmp_path / 'ws'
        root.mkdir()
        skills_dir = root / '.claude' / 'skills'

        # Create user skill
        skill = skills_dir / 'remote-skill'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text('---\ndescription: Remote\n---\n# R')

        # Write lockfile marking it as imported
        lockfile = {
            'version': 1,
            'skills': {
                'remote-skill': {
                    'source': 'url',
                    'name': 'remote-skill',
                    'origin_url': ('https://github.com/test/repo'),
                },
            },
        }
        (skills_dir / 'skills.lock.json').write_text(json.dumps(lockfile))

        (root / 'knowledge').mkdir(parents=True, exist_ok=True)
        (root / 'stores').mkdir(parents=True, exist_ok=True)
        (root / '.git').mkdir(parents=True, exist_ok=True)

        mgr = WorkspaceManager(root)
        result = await mgr.get_structured()

        skill_res = result['skills'][0]
        assert skill_res['slug'] == 'remote-skill'
        assert skill_res['source'] == 'imported'
        assert skill_res['origin_url'] == 'https://github.com/test/repo'


class TestDeleteSkill:
    """Test delete_skill() removes directory and lockfile entry."""

    async def test_delete_skill(self, tmp_path):
        root = tmp_path / 'ws'
        root.mkdir()
        skills_dir = root / '.claude' / 'skills'
        skill = skills_dir / 'to-delete'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text('# Delete me')
        (root / '.git').mkdir()

        mgr = WorkspaceManager(root)
        # Write lockfile entry
        lockfile = {
            'version': 1,
            'skills': {
                'to-delete': {
                    'source': 'local',
                    'name': 'to-delete',
                },
            },
        }
        mgr._write_lockfile(lockfile)

        await mgr.delete_skill('to-delete')
        assert not skill.exists()
        result = mgr._read_lockfile()
        assert 'to-delete' not in result['skills']

    async def test_delete_dotprefix_rejected(self, tmp_path):
        root = tmp_path / 'ws'
        root.mkdir()
        (root / '.claude' / 'skills').mkdir(parents=True)
        (root / '.git').mkdir()
        mgr = WorkspaceManager(root)
        with pytest.raises(ValueError, match='Invalid skill slug'):
            await mgr.delete_skill('.hidden')

    async def test_delete_nonexistent_raises(self, tmp_path):
        root = tmp_path / 'ws'
        root.mkdir()
        (root / '.claude' / 'skills').mkdir(parents=True)
        (root / '.git').mkdir()
        mgr = WorkspaceManager(root)
        with pytest.raises(FileNotFoundError):
            await mgr.delete_skill('no-such-skill')
