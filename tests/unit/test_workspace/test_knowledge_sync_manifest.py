"""Unit tests for MANIFEST-based knowledge sync.

Verifies:
  - Only files listed in MANIFEST.txt are synced
  - __pycache__ is never synced even without MANIFEST
  - Extra files (scripts, etc.) are excluded
"""

import pytest

from app.workspace.knowledge_sync import KnowledgeSyncManager

pytestmark = pytest.mark.unit


def _make_source(tmp_path):
    """Create a fake app/knowledge/ source directory."""
    src = tmp_path / 'src'
    common = src / 'common'
    common.mkdir(parents=True)
    return src


class TestManifestSync:
    async def test_only_manifest_files_synced(self, tmp_path, monkeypatch):
        src = _make_source(tmp_path)

        # Files listed in MANIFEST
        (src / 'README.md').write_text('# README')
        (src / 'CATALOG.md').write_text('# Catalog')
        (src / 'common' / 'amazon-sites.md').write_text('# Amazon')

        # File NOT in MANIFEST
        (src / 'generate_catalog.sh').write_text('#!/bin/bash')

        # MANIFEST.txt
        (src / 'MANIFEST.txt').write_text(
            'README.md\nCATALOG.md\ncommon/amazon-sites.md\n'
        )

        dest = tmp_path / 'dest'
        mgr = KnowledgeSyncManager()
        mgr._dest_dir = dest
        monkeypatch.setattr(mgr, '_get_local_source', lambda: src)

        result = await mgr.fetch()
        assert result['synced'] is True
        assert result['copied'] == 3

        assert (dest / 'README.md').exists()
        assert (dest / 'CATALOG.md').exists()
        assert (dest / 'common' / 'amazon-sites.md').exists()
        assert not (dest / 'generate_catalog.sh').exists()

    async def test_pycache_never_synced(self, tmp_path, monkeypatch):
        """__pycache__ is excluded even without MANIFEST."""
        src = _make_source(tmp_path)

        (src / 'README.md').write_text('# README')
        pycache = src / '__pycache__'
        pycache.mkdir()
        (pycache / 'gen.cpython-313.pyc').write_bytes(b'\x00')

        # No MANIFEST.txt — fallback to rglob
        dest = tmp_path / 'dest'
        mgr = KnowledgeSyncManager()
        mgr._dest_dir = dest
        monkeypatch.setattr(mgr, '_get_local_source', lambda: src)

        result = await mgr.fetch()
        assert result['synced'] is True

        assert (dest / 'README.md').exists()
        assert not (dest / '__pycache__').exists()

    async def test_manifest_with_comments_and_blanks(
        self, tmp_path, monkeypatch
    ):
        src = _make_source(tmp_path)
        (src / 'README.md').write_text('# README')
        (src / 'MANIFEST.txt').write_text('# Knowledge files\n\nREADME.md\n\n')

        dest = tmp_path / 'dest'
        mgr = KnowledgeSyncManager()
        mgr._dest_dir = dest
        monkeypatch.setattr(mgr, '_get_local_source', lambda: src)

        result = await mgr.fetch()
        assert result['copied'] == 1
        assert (dest / 'README.md').exists()

    async def test_manifest_missing_file_skipped(self, tmp_path, monkeypatch):
        """MANIFEST lists a file that doesn't exist — skip it."""
        src = _make_source(tmp_path)
        (src / 'README.md').write_text('# README')
        (src / 'MANIFEST.txt').write_text('README.md\nno-such-file.md\n')

        dest = tmp_path / 'dest'
        mgr = KnowledgeSyncManager()
        mgr._dest_dir = dest
        monkeypatch.setattr(mgr, '_get_local_source', lambda: src)

        result = await mgr.fetch()
        assert result['copied'] == 1

    async def test_unchanged_files_skipped(self, tmp_path, monkeypatch):
        src = _make_source(tmp_path)
        (src / 'README.md').write_text('# README')
        (src / 'MANIFEST.txt').write_text('README.md\n')

        dest = tmp_path / 'dest'
        dest.mkdir()
        (dest / 'README.md').write_text('# README')  # same content

        mgr = KnowledgeSyncManager()
        mgr._dest_dir = dest
        monkeypatch.setattr(mgr, '_get_local_source', lambda: src)

        result = await mgr.fetch()
        assert result['copied'] == 0
        assert result['skipped'] == 1
