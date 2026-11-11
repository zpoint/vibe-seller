"""Pure skill-gate helpers — a leaf module (stdlib + app.config only).

Extracted from ``claude_backend_utils`` so the exit-gate machinery
(``stop_gates``) can resolve a skill's declared gates and locate its
SKILL.md WITHOUT importing ``claude_backend_utils`` (which pulls in
bash_safety, the DB, models and the workspace manager). That import edge
was the one real cycle in the plugin/gate wiring; keeping these three
pure functions in a leaf breaks it by design rather than with a lazy
import.
"""

from pathlib import Path
import re

from app.config import VIBE_SELLER_DIR

_GATES_RE = re.compile(r'^gates:\s*\[([^\]]*)\]\s*$', re.MULTILINE)


def parse_skill_gates(skill_md_path: Path) -> list[str]:
    """Return the exit-gate names this SKILL.md declares.

    Reads YAML frontmatter, looks for ``gates: [a, b]`` (same
    inline-list format as ``requires:``). Returns ``[]`` if the file
    is missing, has no frontmatter, or declares no gates. Resolved
    against ``stop_gates.get_registered_gates`` at submit time — the
    skill names WHICH gates apply to its outputs; the gates
    themselves stay server-side code.
    """
    if not skill_md_path.is_file():
        return []
    try:
        text = skill_md_path.read_text(encoding='utf-8')
    except OSError:
        return []
    if not text.startswith('---\n'):
        return []
    end = text.find('\n---\n', 4)
    if end == -1:
        return []
    m = _GATES_RE.search(text[: end + 5])
    if not m:
        return []
    return [
        item.strip().strip('"\'')
        for item in m.group(1).split(',')
        if item.strip()
    ]


_SKILL_MD_READ_RE = re.compile(r'(?:^|/)skills/([^/]+)/SKILL\.md$')
# A concrete SKILL.md printed by a shell reader in command position
# (`cat`, `sed -n`, `head` …). A glob (`skills/*/SKILL.md`) names no
# skill, and a search (`grep … SKILL.md`) is not a reader.
_SKILL_MD_IN_CMD_RE = re.compile(
    r'(?:^|[;&|\n(`]\s*)(?:(?:sudo|time)\s+)*'
    r'(?:cat|less|more|head|tail|sed|bat|nl)\b[^;&|\n]*?'
    r"""(?:^|[/\s'"=])skills/([A-Za-z0-9_.-]+)/SKILL\.md\b"""
)


def skill_name_from_read(tool_name: str, tool_input: dict) -> str | None:
    """Return the skill whose SKILL.md this tool call reads as a file.

    Reading the file is NOT loading the skill. Claude Code has exactly
    one load path — the ``Skill`` tool, which registers the skill with
    the session — and skill-declared exit gates bind on that alone. A
    read can be anything from following the whole procedure to copying
    one schema, and guessing which binds the wrong gates: in CI an ad
    audit ran ``sed -n '68,90p' …/review-collect/SKILL.md`` for one
    metadata shape, the read bound review-collect, its gate refused the
    ad report for a missing review manifest, and the agent spent its
    budget collecting product reviews nobody asked for. The hook uses
    this only to tell the agent which path loads (see
    :func:`unloaded_skill_read_hint`).
    """
    if tool_name == 'Read':
        path = tool_input.get('file_path', '')
        if not isinstance(path, str) or not path:
            return None
        m = _SKILL_MD_READ_RE.search(path)
        return m.group(1) if m else None
    if tool_name == 'Bash':
        m = _SKILL_MD_IN_CMD_RE.search(str(tool_input.get('command', '')))
        return m.group(1) if m else None
    return None


def unloaded_skill_read_hint(
    tool_name: str, tool_input: dict, loaded: set[str] | frozenset[str]
) -> str | None:
    """Context for a read of a SKILL.md the session has not loaded.

    Closes the gap the read-binding used to paper over: an agent that
    ``cat``-s a skill and follows it would run with none of its gates.
    Instead of guessing intent, say once, at the read, which path
    loads. A lookup carries on; a run that means to follow the skill
    calls the ``Skill`` tool and gets its gates the standard way.
    """
    name = skill_name_from_read(tool_name, tool_input)
    if not name or name in loaded:
        return None
    return (
        f'You read {name}/SKILL.md as a plain file. That does not load '
        f'the skill: its checks and prerequisites are not active. If '
        f'you only needed a snippet, carry on. If you mean to FOLLOW '
        f"this skill's procedure, load it first with the Skill tool "
        f"(skill='{name}')."
    )


def find_skill_md(workspace_dir: Path, skill_name: str) -> Path | None:
    """Locate ``{workspace}/.claude/skills/{skill_name}/SKILL.md``.

    Falls back to ``~/.vibe-seller/.claude/skills/`` if the per-task
    workspace copy is missing (e.g. a CLI-builtin skill we don't ship).
    Returns None if neither exists.
    """
    candidates = [
        workspace_dir / '.claude' / 'skills' / skill_name / 'SKILL.md',
        VIBE_SELLER_DIR / '.claude' / 'skills' / skill_name / 'SKILL.md',
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None
