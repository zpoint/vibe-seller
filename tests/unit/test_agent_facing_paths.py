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

RULE for future skills_v2 files: a file that is NEW since the oldest
supported release (i.e. one remote skill sync can deliver to clients
whose runtime predates it) must phrase the downloads folder
VERSION-TOLERANTLY — reference ``$VIBE_STORE_DOWNLOADS`` but say what
to do when the variable is empty (see custom-analytics.md for the
shape). The knowledge tree has a stricter rule: it is synced to every
client and stays version-neutral, so it must never mention the
variable at all (pinned by the K1 test below).
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
# copy verbatim into a shell command. Plus the CDP method that must
# never be NAMED in agent-facing text (F3): "never call X" still
# introduces X as the available verb — the observed trigger chain had
# agents "helpfully" calling it after reading such a sentence.
FORBIDDEN_LITERALS = (
    '~/.vibe-seller',
    '$HOME/.vibe-seller',
    'setDownloadBehavior',
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


# ── K1: the version-neutral trees never mention the env var ─────────
#
# app/knowledge is synced to EVERY client (including old releases that
# never export VIBE_STORE_DOWNLOADS), and app/skills is the frozen
# legacy tree — a mention of the variable there would be a dangling
# reference for exactly the clients that read those trees.


def _md_files_mentioning(root: Path, needle: str) -> list[Path]:
    """Every file under *root* whose text contains *needle*.

    Scans ALL files, not just markdown: the K1 rule is "no file under
    the version-neutral trees mentions the variable". ``errors=ignore``
    keeps a stray binary from failing the scan itself.
    """
    if not root.is_dir():
        return []
    return [
        f
        for f in sorted(root.rglob('*'))
        if f.is_file()
        and needle in f.read_text(encoding='utf-8', errors='ignore')
    ]


@pytest.mark.unit
def test_knowledge_tree_never_mentions_the_env_var():
    """app/knowledge must stay version-neutral (K1)."""
    offenders = _md_files_mentioning(
        BASE_DIR / 'app' / 'knowledge', 'VIBE_STORE_DOWNLOADS'
    )
    assert not offenders, (
        'app/knowledge is synced to every client, including releases '
        'that never export VIBE_STORE_DOWNLOADS; these files mention '
        f'the variable: {[str(o) for o in offenders]}'
    )


@pytest.mark.unit
def test_frozen_legacy_skills_never_mention_the_env_var():
    """app/skills is frozen for pre-0.13 clients (K1)."""
    offenders = _md_files_mentioning(
        BASE_DIR / 'app' / 'skills', 'VIBE_STORE_DOWNLOADS'
    )
    assert not offenders, (
        'app/skills is the frozen legacy tree; these files mention '
        'VIBE_STORE_DOWNLOADS, which those clients never export: '
        f'{[str(o) for o in offenders]}'
    )


@pytest.mark.unit
def test_k1_negative_control_planted_token_is_caught(tmp_path):
    """The K1 scan actually catches a mention: plant the token in a
    temp tree and the same helper flags it (proves the tests above
    fail, not silently pass, if the token lands in a scanned tree)."""
    planted = tmp_path / 'common' / 'planted.md'
    planted.parent.mkdir(parents=True)
    planted.write_text(
        'use $VIBE_STORE_DOWNLOADS for downloads' + '\n',
        encoding='utf-8',
    )
    offenders = _md_files_mentioning(tmp_path, 'VIBE_STORE_DOWNLOADS')
    assert offenders == [planted]


@pytest.mark.unit
def test_f3_negative_control_planted_method_name_is_caught(tmp_path):
    """The forbidden-literal scan catches the CDP method name: plant
    it in a temp tree and the same helper flags it (proves the guards
    above fail, not silently pass, if the token returns)."""
    planted = tmp_path / 'planted.md'
    planted.write_text(
        'never point setDownloadBehavior elsewhere', encoding='utf-8'
    )
    offenders = _md_files_mentioning(tmp_path, 'setDownloadBehavior')
    assert offenders == [planted]
