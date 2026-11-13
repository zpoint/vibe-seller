"""Unit tests for ``app.skill_pack`` — the directory→archive half.

The packer is checked the way the extractor is: round-trip through
``extract_archive`` (the two halves of one contract), plus every walk
refusal the docstring promises. These are pure-logic tests (no HTTP,
no workspace); the workflow tier drives the whole tool path.
"""

import io
import os
from pathlib import Path
import tarfile

import pytest

from app import skill_uploads
import app.skill_pack as sp
from app.skill_pack import PackError, pack_skill_dir, pack_tar_gz

pytestmark = pytest.mark.unit

SKILL_MD = b'---\nname: pack-me\ndescription: d\n---\n# pack-me\n'
PNG = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
    b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01'
    b'\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
)


def _make_dir(root: Path) -> Path:
    d = root / 'invoice-check'
    (d / 'assets').mkdir(parents=True)
    (d / 'references').mkdir()
    (d / 'scripts').mkdir()
    (d / 'SKILL.md').write_bytes(SKILL_MD)
    (d / 'references' / 'notes.md').write_text('nested text\n')
    (d / 'assets' / 'icon.png').write_bytes(PNG)
    run = d / 'scripts' / 'run.sh'
    run.write_text('#!/bin/sh\necho hi\n')
    run.chmod(0o755)
    return d


def _extract(archive: bytes, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    staged = out_dir / 'archive.tar.gz'
    staged.write_bytes(archive)
    return skill_uploads.extract_archive(staged, 'tar.gz')


class TestRoundTrip:
    def test_tree_lands_byte_identical_with_exec_bit(self, tmp_path):
        d = _make_dir(tmp_path)
        extracted = _extract(pack_skill_dir(d), tmp_path / 'out')

        assert extracted.skill_md == SKILL_MD.decode()
        assert extracted.files['references/notes.md'] == b'nested text\n'
        assert extracted.files['assets/icon.png'] == PNG
        assert extracted.executables == {'scripts/run.sh'}

    def test_exec_bits_survive_via_mode(self):
        archive = pack_tar_gz({'a.sh': b'x', 'b.md': b'y'}, {'a.sh'})
        buf = io.BytesIO(archive)
        with tarfile.open(fileobj=buf, mode='r:gz') as tar:
            modes = {m.name: m.mode for m in tar}
        assert modes['a.sh'] & 0o111
        assert not modes['b.md'] & 0o111


class TestWalkRefusals:
    def test_no_skill_md_at_root(self, tmp_path):
        (tmp_path / 'empty-skill').mkdir()
        (tmp_path / 'empty-skill' / 'readme.md').write_text('x')
        with pytest.raises(PackError, match='SKILL.md at its root'):
            pack_skill_dir(tmp_path / 'empty-skill')

    def test_not_a_directory(self, tmp_path):
        with pytest.raises(PackError, match='not a directory'):
            pack_skill_dir(tmp_path / 'nope')

    def test_symlink_escaping_the_dir_is_refused(self, tmp_path):
        d = _make_dir(tmp_path)
        secret = tmp_path / 'secret.txt'
        secret.write_text('leaky')
        (d / 'link.txt').symlink_to(secret)
        with pytest.raises(PackError, match='escapes the skill directory'):
            pack_skill_dir(d)

    def test_symlink_pointing_inside_is_dereferenced(self, tmp_path):
        d = _make_dir(tmp_path)
        (d / 'copy.md').symlink_to(d / 'references' / 'notes.md')
        extracted = _extract(pack_skill_dir(d), tmp_path / 'out')
        assert extracted.files['copy.md'] == b'nested text\n'

    def test_symlinked_subdir_is_refused(self, tmp_path):
        d = _make_dir(tmp_path)
        outside = tmp_path / 'elsewhere'
        outside.mkdir()
        (outside / 'x.md').write_text('x')
        (d / 'sub').symlink_to(outside)
        with pytest.raises(PackError, match='symlinked subdirectory'):
            pack_skill_dir(d)

    def test_fifo_is_refused(self, tmp_path):
        d = _make_dir(tmp_path)
        os.mkfifo(d / 'pipe')
        with pytest.raises(PackError, match='only regular files'):
            pack_skill_dir(d)

    def test_oversize_file_refused_by_name(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sp, 'MAX_SKILL_FILE_BYTES', 30)
        d = _make_dir(tmp_path)
        with pytest.raises(PackError, match='per-file'):
            pack_skill_dir(d)

    def test_file_count_cap_refused(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sp, 'MAX_SKILL_FILES', 2)
        d = _make_dir(tmp_path)
        with pytest.raises(PackError, match='at most 2 files'):
            pack_skill_dir(d)


class TestJunkStaysExtractorsProblem:
    def test_macos_junk_is_packed_and_extraction_reports_it(self, tmp_path):
        d = _make_dir(tmp_path)
        (d / '.DS_Store').write_bytes(b'junk')
        (d / 'assets' / '._icon.png').write_bytes(b'junk')
        extracted = _extract(pack_skill_dir(d), tmp_path / 'out')
        assert sorted(extracted.ignored) == ['.DS_Store', 'assets/._icon.png']
        assert 'assets/icon.png' in extracted.files
