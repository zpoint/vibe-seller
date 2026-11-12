"""Unit tests for the skill import contract (``save_skill`` writes).

The external MCP ``manage_skill(action=import)`` flow ends here: one
call installs a whole skill — SKILL.md, text files, binary files
(base64, byte-identical), executable bits — and every rule is checked
BEFORE the first byte lands. These pin the contract on the REAL
WorkspaceManager (the workflow tests pin the tool dispatch on a mock).
"""

import base64
import json
import os

import pytest

from app.workspace import skills_manager
from app.workspace.manager import WorkspaceManager
from app.workspace.skills_manager import (
    derive_skill_slug,
    parse_yaml_frontmatter,
    skill_slug,
)

pytestmark = pytest.mark.unit

# 1x1 PNG — a genuinely binary file (invalid UTF-8).
PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk'
    'YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='
)

# Codex-style frontmatter: a ``metadata:`` block the import must keep
# byte-identical (it is not ours to interpret).
SKILL_MD = (
    '---\nname: Amazon Listing Add\n'
    'description: "Draft a listing upload file."\n'
    'metadata:\n  short-description: Draft an Amazon listing upload\n'
    '---\n\n# Amazon Listing Add\n\nBody.\n'
)


def _mgr(tmp_path, synced=None):
    """A WorkspaceManager on a bare tmp workspace."""
    root = tmp_path / 'ws'
    skills_dir = root / '.claude' / 'skills'
    skills_dir.mkdir(parents=True)
    (root / '.git').mkdir()
    if synced:
        (skills_dir / '.sync_meta.json').write_text(
            json.dumps({'synced_skills': synced})
        )
    return WorkspaceManager(root), skills_dir


class TestImportRoundTrip:
    async def test_binary_lands_byte_identical(self, tmp_path):
        mgr, skills_dir = _mgr(tmp_path)
        result = await mgr.save_skill(
            'amazon-listing-add',
            SKILL_MD,
            files={'references/notes.md': '# notes'},
            files_bin={'assets/template.png': base64.b64encode(PNG).decode()},
        )
        assert result['action'] == 'created'

        skill_dir = skills_dir / 'amazon-listing-add'
        assert (skill_dir / 'SKILL.md').read_text() == SKILL_MD
        assert (skill_dir / 'references' / 'notes.md').read_text() == '# notes'
        assert (skill_dir / 'assets' / 'template.png').read_bytes() == PNG

    async def test_nested_dirs_are_created(self, tmp_path):
        mgr, skills_dir = _mgr(tmp_path)
        await mgr.save_skill(
            'deep',
            SKILL_MD,
            files_bin={
                'a/b/c/data.bin': base64.b64encode(b'\x00\x01').decode()
            },
        )
        assert (
            skills_dir / 'deep' / 'a' / 'b' / 'c' / 'data.bin'
        ).read_bytes() == b'\x00\x01'

    async def test_executables_get_the_bit_and_others_do_not(self, tmp_path):
        mgr, skills_dir = _mgr(tmp_path)
        await mgr.save_skill(
            'with-scripts',
            SKILL_MD,
            files={'scripts/run.sh': '#!/bin/sh\n', 'references/n.md': 'x'},
            executables=['scripts/run.sh'],
        )
        skill_dir = skills_dir / 'with-scripts'
        run_mode = os.stat(skill_dir / 'scripts' / 'run.sh').st_mode
        note_mode = os.stat(skill_dir / 'references' / 'n.md').st_mode
        assert run_mode & 0o111
        assert not note_mode & 0o111


class TestImportValidationIsAtomic:
    async def test_traversal_and_escapes_refused(self, tmp_path):
        mgr, skills_dir = _mgr(tmp_path)
        for bad in [
            '../escape.md',
            '/abs.md',
            'a/../../x',
            'a\\b.md',
            'skill.md',  # case-insensitive SKILL.md collision
            'SKILL.md',
            '',
        ]:
            with pytest.raises(ValueError, match='file path'):
                await mgr.save_skill('ok-slug', SKILL_MD, files={bad: 'x'})
            with pytest.raises(ValueError, match='file path'):
                await mgr.save_skill(
                    'ok-slug',
                    SKILL_MD,
                    files_bin={bad: base64.b64encode(b'x').decode()},
                )
        assert not (skills_dir / 'ok-slug').exists()

    async def test_a_refused_file_writes_nothing(self, tmp_path):
        """Atomic create: validate first, then write — a bad path in
        the third file leaves no stub skill behind."""
        mgr, skills_dir = _mgr(tmp_path)
        with pytest.raises(ValueError, match='file path'):
            await mgr.save_skill(
                'half-import',
                SKILL_MD,
                files={'a.md': 'fine', '../boom.md': 'bad'},
            )
        assert not (skills_dir / 'half-import').exists()

    async def test_invalid_base64_refused(self, tmp_path):
        mgr, _ = _mgr(tmp_path)
        with pytest.raises(ValueError, match='base64'):
            await mgr.save_skill(
                'bad-b64', SKILL_MD, files_bin={'x.png': 'not base64!'}
            )

    async def test_file_in_both_maps_refused(self, tmp_path):
        mgr, _ = _mgr(tmp_path)
        with pytest.raises(ValueError, match='not both'):
            await mgr.save_skill(
                'dupe',
                SKILL_MD,
                files={'x.md': 'text'},
                files_bin={'x.md': base64.b64encode(b'bin').decode()},
            )

    async def test_executable_must_be_a_written_file(self, tmp_path):
        mgr, _ = _mgr(tmp_path)
        with pytest.raises(ValueError, match='not one of'):
            await mgr.save_skill(
                'exec-list',
                SKILL_MD,
                files={'a.md': 'x'},
                executables=['b.md'],
            )

    async def test_size_caps(self, tmp_path, monkeypatch):
        mgr, _ = _mgr(tmp_path)
        monkeypatch.setattr(skills_manager, 'MAX_SKILL_FILE_BYTES', 8)
        with pytest.raises(ValueError, match='per-file'):
            await mgr.save_skill(
                'big-file', SKILL_MD, files={'big.md': 'x' * 9}
            )
        monkeypatch.setattr(skills_manager, 'MAX_SKILL_TOTAL_BYTES', 32)
        with pytest.raises(ValueError, match='total size'):
            await mgr.save_skill(
                'big-total',
                SKILL_MD,
                files={'a.md': 'x' * 8, 'b.md': 'x' * 8, 'c.md': 'x' * 8},
            )
        monkeypatch.setattr(skills_manager, 'MAX_SKILL_FILES', 2)
        with pytest.raises(ValueError, match='at most'):
            await mgr.save_skill(
                'many',
                SKILL_MD,
                files={'a.md': 'x', 'b.md': 'x', 'c.md': 'x'},
            )

    async def test_builtin_slug_refused_with_binaries_too(self, tmp_path):
        """HARD INVARIANT, binary path: built-ins stay read-only."""
        mgr, skills_dir = _mgr(tmp_path, synced=['amazon-listing'])
        (skills_dir / 'amazon-listing').mkdir()
        (skills_dir / 'amazon-listing' / 'SKILL.md').write_text('# builtin')
        with pytest.raises(ValueError, match='built-in'):
            await mgr.save_skill(
                'amazon-listing',
                SKILL_MD,
                files_bin={'x.png': base64.b64encode(PNG).decode()},
            )
        assert (
            skills_dir / 'amazon-listing' / 'SKILL.md'
        ).read_text() == '# builtin'
        assert not (skills_dir / 'amazon-listing' / 'x.png').exists()


class TestDeriveSkillSlug:
    def test_frontmatter_name_maps_to_the_slug_rule(self):
        assert derive_skill_slug(SKILL_MD) == 'amazon-listing-add'

    def test_matches_create_skill_normalisation(self):
        name = 'Revenue Report v2'
        assert derive_skill_slug(f'---\nname: {name}\n---\n') == skill_slug(
            name
        )

    def test_no_frontmatter_name_is_empty(self):
        assert derive_skill_slug('# Just a body\n') == ''
        assert derive_skill_slug('---\ndescription: x\n---\n') == ''

    def test_metadata_block_does_not_confuse_the_parser(self):
        fm = parse_yaml_frontmatter(SKILL_MD)
        assert fm['name'] == 'Amazon Listing Add'
        assert fm['short-description'] == 'Draft an Amazon listing upload'


class TestReplaceVsMerge:
    async def test_replace_removes_stale_files(self, tmp_path):
        mgr, skills_dir = _mgr(tmp_path)
        await mgr.save_skill('re', SKILL_MD, files={'old.md': 'x'})
        result = await mgr.save_skill(
            're',
            SKILL_MD,
            files={'new.md': 'y'},
            replace=True,
            overwrite=True,
        )
        assert result['action'] == 'updated'
        skill_dir = skills_dir / 're'
        assert not (skill_dir / 'old.md').exists(), (
            'replace must delete files the new tree does not carry'
        )
        assert (skill_dir / 'new.md').read_text() == 'y'
        assert os.stat(skill_dir).st_mode & 0o777 == 0o755, (
            'the skill dir must be readable like every other skill dir'
        )

    async def test_merge_keeps_stale_files(self, tmp_path):
        mgr, skills_dir = _mgr(tmp_path)
        await mgr.save_skill('mg', SKILL_MD, files={'old.md': 'x'})
        await mgr.save_skill('mg', SKILL_MD, files={'new.md': 'y'})
        skill_dir = skills_dir / 'mg'
        assert (skill_dir / 'old.md').read_text() == 'x'
        assert (skill_dir / 'new.md').read_text() == 'y'

    async def test_refused_replace_leaves_the_old_skill_untouched(
        self, tmp_path
    ):
        mgr, skills_dir = _mgr(tmp_path)
        await mgr.save_skill('re', SKILL_MD, files={'old.md': 'x'})
        with pytest.raises(ValueError, match='file path'):
            await mgr.save_skill(
                're',
                SKILL_MD,
                files={'../boom.md': 'b'},
                replace=True,
                overwrite=True,
            )
        assert (skills_dir / 're' / 'old.md').read_text() == 'x'
        leftovers = [
            p.name for p in skills_dir.iterdir() if p.name.startswith('.re-')
        ]
        assert leftovers == [], leftovers

    async def test_replace_a_symlinked_skill_swaps_in_a_real_dir(
        self, tmp_path
    ):
        """A symlinked custom skill is replaced by a real tree — and
        the link's target is left exactly as it was."""
        mgr, skills_dir = _mgr(tmp_path)
        real = tmp_path / 'external-skill'
        real.mkdir()
        (real / 'SKILL.md').write_text('# external')
        (real / 'keep-me.md').write_text('target file')
        (skills_dir / 'linked').symlink_to(real)

        await mgr.save_skill(
            'linked',
            SKILL_MD,
            files={'new.md': 'y'},
            replace=True,
            overwrite=True,
        )
        assert not (skills_dir / 'linked').is_symlink()
        assert (skills_dir / 'linked' / 'new.md').read_text() == 'y'
        assert (real / 'keep-me.md').read_text() == 'target file'
        assert (real / 'SKILL.md').read_text() == '# external'


class TestSymlinkEscape:
    async def test_subdir_symlink_out_of_the_skill_refused(self, tmp_path):
        mgr, skills_dir = _mgr(tmp_path)
        await mgr.save_skill('sym', SKILL_MD)
        outside = tmp_path / 'outside'
        outside.mkdir()
        (skills_dir / 'sym' / 'evil').symlink_to(outside)

        with pytest.raises(ValueError, match='symlink'):
            await mgr.save_skill('sym', SKILL_MD, files={'evil/x.md': 'pwn'})
        assert not (outside / 'x.md').exists()

    async def test_file_symlink_out_of_the_skill_refused(self, tmp_path):
        mgr, skills_dir = _mgr(tmp_path)
        await mgr.save_skill('sym2', SKILL_MD)
        victim = tmp_path / 'victim.md'
        victim.write_text('original')
        (skills_dir / 'sym2' / 'note.md').symlink_to(victim)

        with pytest.raises(ValueError, match='escape'):
            await mgr.save_skill('sym2', SKILL_MD, files={'note.md': 'pwn'})
        assert victim.read_text() == 'original'

    async def test_skill_md_symlink_out_of_the_skill_refused(self, tmp_path):
        mgr, skills_dir = _mgr(tmp_path)
        await mgr.save_skill('sym3', SKILL_MD)
        victim = tmp_path / 'victim-md.md'
        victim.write_text('original')
        (skills_dir / 'sym3' / 'SKILL.md').unlink()
        (skills_dir / 'sym3' / 'SKILL.md').symlink_to(victim)

        with pytest.raises(ValueError, match='escape'):
            await mgr.save_skill('sym3', 'pwned')
        assert victim.read_text() == 'original'


class TestProvenance:
    async def test_origin_url_marks_the_skill_imported(self, tmp_path):
        mgr, _ = _mgr(tmp_path)
        await mgr.save_skill(
            'prov', SKILL_MD, replace=True, origin_url='local skill dir'
        )
        lock = mgr._read_lockfile()['skills']['prov']
        assert lock['source'] == 'url'
        assert lock['origin_url'] == 'local skill dir'
        skills = {s['slug']: s for s in await mgr.list_skills()}
        assert skills['prov']['source'] == 'imported'

    async def test_no_origin_url_keeps_a_local_skill_local(self, tmp_path):
        mgr, _ = _mgr(tmp_path)
        await mgr.save_skill('plain', SKILL_MD)
        skills = {s['slug']: s for s in await mgr.list_skills()}
        assert skills['plain']['source'] == 'custom'


class TestOverwriteGate:
    """A replace over an EXISTING user skill is a destructive act on
    someone's work: refused without ``overwrite=True``, naming what
    is there and how to proceed."""

    async def test_refused_without_overwrite_and_old_skill_untouched(
        self, tmp_path
    ):
        mgr, skills_dir = _mgr(tmp_path)
        await mgr.save_skill('mine', SKILL_MD, files={'hand-made.md': 'work'})

        with pytest.raises(ValueError) as excinfo:
            await mgr.save_skill(
                'mine', SKILL_MD.replace('Body.', 'New.'), replace=True
            )
        message = str(excinfo.value)
        assert 'a skill named mine already exists (source=custom)' in message
        assert 'overwrite=true' in message
        assert 'choose another slug' in message

        skill_dir = skills_dir / 'mine'
        assert (skill_dir / 'hand-made.md').read_text() == 'work'
        assert 'New.' not in (skill_dir / 'SKILL.md').read_text()

    async def test_error_names_an_imported_skills_source(self, tmp_path):
        mgr, _ = _mgr(tmp_path)
        await mgr.save_skill(
            'from-elsewhere',
            SKILL_MD,
            origin_url='local skill dir',
        )
        with pytest.raises(ValueError, match='source=imported'):
            await mgr.save_skill('from-elsewhere', SKILL_MD, replace=True)

    async def test_overwrite_true_replaces(self, tmp_path):
        mgr, skills_dir = _mgr(tmp_path)
        await mgr.save_skill('mine', SKILL_MD, files={'old.md': 'x'})
        result = await mgr.save_skill(
            'mine',
            SKILL_MD,
            files={'new.md': 'y'},
            replace=True,
            overwrite=True,
        )
        assert result['action'] == 'updated'
        skill_dir = skills_dir / 'mine'
        assert not (skill_dir / 'old.md').exists()
        assert (skill_dir / 'new.md').read_text() == 'y'

    async def test_fresh_import_needs_no_overwrite(self, tmp_path):
        mgr, skills_dir = _mgr(tmp_path)
        result = await mgr.save_skill('brand-new', SKILL_MD, replace=True)
        assert result['action'] == 'created'
        assert (skills_dir / 'brand-new' / 'SKILL.md').is_file()

    async def test_merge_update_is_unaffected_by_the_gate(self, tmp_path):
        """update keeps its merge semantics — no overwrite needed to
        extend a skill that exists."""
        mgr, skills_dir = _mgr(tmp_path)
        await mgr.save_skill('ext', SKILL_MD, files={'kept.md': 'k'})
        await mgr.save_skill('ext', SKILL_MD, files={'added.md': 'a'})
        skill_dir = skills_dir / 'ext'
        assert (skill_dir / 'kept.md').is_file()
        assert (skill_dir / 'added.md').is_file()
