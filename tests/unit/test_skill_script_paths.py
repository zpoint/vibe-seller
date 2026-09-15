"""Skill docs must address their scripts by a path that exists at runtime.

The agent's CWD is the per-task workspace (`claude_backend.py` passes
``cwd=str(ws_dir)``), and skills are copied in under
``.claude/skills/<name>/``. So a documented ``python scripts/foo.py``
resolves to ``<workspace>/scripts/foo.py``, which does not exist —
verified against a real task workspace, where ``scripts/ads_bulk.py`` is
absent while ``.claude/skills/amazon-ads/scripts/ads_bulk.py`` is there.

A doc bug like that fails only when an agent happens to reach that step,
which is why one sat in a shipped skill unnoticed. These tests make the
class cheap to catch instead.
"""

from pathlib import Path
import re

import pytest

pytestmark = pytest.mark.unit

_SKILLS = Path(__file__).resolve().parents[2] / 'app' / 'skills_v2'
_DOCS = sorted(_SKILLS.glob('*/SKILL.md')) + sorted(
    _SKILLS.glob('*/references/*.md')
)

# ``python scripts/foo.py`` / ``python3 scripts/foo.py`` — a workspace
# relative path that cannot resolve.
_BARE = re.compile(r'python3?\s+scripts/[\w./-]+\.py')
# A synced script reference, in either documented form:
#   direct   — `.claude/skills/<name>/scripts/<file>.py`
#   two-line — `S=.claude/skills/<name>/scripts` … then `$S/<file>.py`
# The two-line form is the dominant convention, so a matcher blind to it
# would silently cover almost nothing.
_LITERAL = re.compile(r'\.claude/skills/([\w-]+)/scripts/([\w./-]+\.py)')
_SVAR = re.compile(r'S=\.claude/skills/([\w-]+)/scripts\b')
_SUSE = re.compile(r'\$S/([\w./-]+\.py)')


def _bare_hits():
    """Yield ``(doc, line, text)`` for each bare ``scripts/`` command."""
    for doc in _DOCS:
        text = doc.read_text(encoding='utf-8')
        for m in _BARE.finditer(text):
            yield doc, text[: m.start()].count('\n') + 1, m.group(0)


def _script_refs():
    """Yield ``(doc, line, skill, script)`` for every synced reference."""
    for doc in _DOCS:
        text = doc.read_text(encoding='utf-8')
        for m in _LITERAL.finditer(text):
            yield doc, text[: m.start()].count('\n') + 1, m.group(1), m.group(2)
        # ``$S/foo.py`` binds to the most recent ``S=`` above it.
        binds = [(m.start(), m.group(1)) for m in _SVAR.finditer(text)]
        for m in _SUSE.finditer(text):
            prior = [name for pos, name in binds if pos < m.start()]
            if prior:
                yield (
                    doc,
                    text[: m.start()].count('\n') + 1,
                    prior[-1],
                    m.group(1),
                )


def test_no_bare_relative_script_invocations():
    """No skill doc may invoke a script via a bare ``scripts/`` path."""
    bad = [
        f'{doc.relative_to(_SKILLS)}:{line}: {hit}'
        for doc, line, hit in _bare_hits()
    ]
    assert not bad, (
        'These resolve against the task workspace, where no scripts/ '
        'directory exists. Use the synced skill path '
        '(.claude/skills/<name>/scripts/...):\n  ' + '\n  '.join(bad)
    )


def test_literal_skill_script_paths_exist():
    """A documented ``.claude/skills/...`` script must exist in-tree."""
    missing = [
        f'{doc.relative_to(_SKILLS)}:{line}: {skill}/scripts/{script}'
        for doc, line, skill, script in _script_refs()
        if not (_SKILLS / skill / 'scripts' / script).is_file()
    ]
    assert not missing, 'Documented script does not exist:\n  ' + '\n  '.join(
        missing
    )


def test_literal_skill_script_paths_are_synced():
    """...and must be in MANIFEST.txt, or it never reaches the client."""
    manifest = set(
        (_SKILLS / 'MANIFEST.txt').read_text(encoding='utf-8').split()
    )
    unsynced = [
        f'{doc.relative_to(_SKILLS)}:{line}: {rel}'
        for doc, line, skill, script in _script_refs()
        if (rel := f'{skill}/scripts/{script}') not in manifest
    ]
    assert not unsynced, (
        'Documented script is missing from MANIFEST.txt, so sync will '
        'not copy it to the client:\n  ' + '\n  '.join(unsynced)
    )
