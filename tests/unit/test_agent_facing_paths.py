"""Agent-facing text must never spell a default-home vibe-seller path.

The store download dir (and every other runtime path) reaches the agent
RESOLVED for this server's ``VIBE_HOME`` — spelled once, in the store
context of the system prompt — or via ``$VIBE_STORE_DOWNLOADS`` in the
agent's environment. Docs that hardcode ``~/.vibe-seller/...`` look
right on a default single-home install and are WRONG on any machine
running more than one vibe-seller home: a live task agent followed such
a doc literally, created the directory in ANOTHER server's home and
repointed ``Page.setDownloadBehavior`` there (2026-11-27 incident).

This test scans the trees an agent reads — skill pages, knowledge
pages, prompt templates — so the hardcoded spelling cannot come back.
``app/skills/`` is the frozen legacy tree for pre-0.13 clients and is
deliberately not scanned; ``.py`` files are not scanned either, because
skill scripts may legitimately re-derive the default home in code
(the same ``VIBE_HOME``-or-``~/.vibe-seller`` fallback ``app.config``
performs) — only prose aimed at the agent is the hazard.
"""

from pathlib import Path

import pytest

from app.config import BASE_DIR

# Trees whose markdown an agent reads at runtime.
SCANNED_DIRS = (
    BASE_DIR / 'app' / 'skills_v2',
    BASE_DIR / 'app' / 'knowledge',
    BASE_DIR / 'app' / 'prompts',
)

# Every spelling of "the default home's vibe-seller dir" an agent might
# copy verbatim into a shell command.
FORBIDDEN_LITERALS = (
    '~/.vibe-seller',
    '$HOME/.vibe-seller',
)


def _agent_facing_markdown() -> list[Path]:
    files: list[Path] = []
    for root in SCANNED_DIRS:
        if not root.is_dir():
            continue
        files.extend(sorted(root.rglob('*.md')))
    return files


def test_agent_facing_trees_exist():
    """Guard the guard: a renamed/moved tree must fail loudly here
    instead of silently scanning nothing."""
    for root in SCANNED_DIRS:
        assert root.is_dir(), f'missing agent-facing tree: {root}'
    assert len(_agent_facing_markdown()) > 10


@pytest.mark.unit
@pytest.mark.parametrize(
    'md_path', _agent_facing_markdown(), ids=lambda p: str(p)
)
def test_no_hardcoded_default_home_path(md_path: Path):
    text = md_path.read_text(encoding='utf-8')
    for literal in FORBIDDEN_LITERALS:
        assert literal not in text, (
            f'{md_path} spells {literal!r} — agent-facing docs must '
            'reference the resolved path ($VIBE_STORE_DOWNLOADS, the '
            'store context, or a workspace-relative path), never a '
            'default-home path that is wrong on multi-home machines'
        )
