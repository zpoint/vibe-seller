"""Skill-tree hosting split: frozen legacy vs the 0.13 line.

Pins the backward-compat invariant behind the browser-use 0.13 migration
(docs/browser-use-0.13-migration.md):

  * ``app/skills`` stays FROZEN as the 0.12.x subcommand-CLI tree — every
    client up to v0.0.7 hardcodes that path and keeps pulling it. If a
    rewrite ever lands there, pre-guard clients (< v0.0.3) break.
  * The active line ships ``app/skills_v2`` (0.13 heredoc/env-var), and all
    three sync references resolve to it via one constant.
"""

import importlib.resources
from pathlib import Path

import pytest

from app import config
from app.workspace.skills_sync import skills_sync

pytestmark = pytest.mark.unit


def _app_dir() -> Path:
    return Path(str(importlib.resources.files('app')))


def test_skills_subdir_is_the_single_source_of_truth():
    # New release line points at skills_v2 everywhere via one constant.
    assert config.SKILLS_SUBDIR == 'skills_v2'
    assert config.SKILLS_REPO_URL.endswith('/app/skills_v2')
    # Local bundled source resolves to the same subdir.
    src = skills_sync._get_local_source()
    assert src is not None and src.name == 'skills_v2'


def test_legacy_skills_tree_is_frozen_012x():
    """app/skills must remain the 0.12.x subcommand-CLI tree."""
    app = _app_dir()
    legacy = app / 'skills' / 'browser-use' / 'SKILL.md'
    assert legacy.is_file(), 'legacy browser-use skill must stay in place'
    text = legacy.read_text()
    # Subcommand CLI = the 0.12.x contract old clients depend on.
    assert 'browser-use open' in text, 'legacy skill must stay 0.12.x'
    # Deprecation note is a top-level file → never synced (absent from
    # MANIFEST, not a skill subdir).
    dep = app / 'skills' / 'DEPRECATED.md'
    assert dep.is_file()
    assert 'DEPRECATED.md' not in (app / 'skills' / 'MANIFEST.txt').read_text()


def test_v2_tree_ships_the_0_13_heredoc_skill():
    app = _app_dir()
    v2 = app / 'skills_v2'
    # Flagship skill renamed to match upstream and rewritten for heredoc.
    assert not (v2 / 'browser-use').exists()
    skill = v2 / 'browser-harness' / 'SKILL.md'
    assert skill.is_file()
    text = skill.read_text()
    assert "<<'PY'" in text and 'new_tab(' in text
    # Manifest points at the renamed skill, not the old one.
    manifest = (v2 / 'MANIFEST.txt').read_text()
    assert 'browser-harness/SKILL.md' in manifest
    assert 'browser-use/SKILL.md' not in manifest
