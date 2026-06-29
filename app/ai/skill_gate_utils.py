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
    skill names WHICH reviewers apply to its outputs; the reviewers
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


def skill_name_from_read(tool_name: str, tool_input: dict) -> str | None:
    """Return the skill name if this tool call Reads a SKILL.md.

    Agents load skills two ways: the ``Skill`` tool (already tracked
    into ``_loaded_skills`` by the prereq hook) and a plain ``Read``
    of ``.claude/skills/<name>/SKILL.md`` (the catalog-driven path).
    Skill-declared exit gates must see both, or a skill loaded via
    Read would silently skip its own reviewers.
    """
    if tool_name != 'Read':
        return None
    path = tool_input.get('file_path', '')
    if not isinstance(path, str) or not path:
        return None
    m = _SKILL_MD_READ_RE.search(path)
    return m.group(1) if m else None


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
