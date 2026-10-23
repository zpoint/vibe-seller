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
# A concrete SKILL.md named anywhere in a shell command (`cat`, `sed -n`,
# `head` …). A glob (`skills/*/SKILL.md`) names no skill and binds none.
#
# Only a READER in command position counts -- the agent printing the
# skill to follow it. A search that merely names the file is not a load:
# in CI a reflection step ran `grep -rn "ad console" …/amazon-ads/SKILL.md
# …/amazon-reports/SKILL.md`, the first cut of this rule bound both
# skills, and the ad review gate then turned a four-number lookup into a
# full audit.
_SKILL_MD_IN_CMD_RE = re.compile(
    r'(?:^|[;&|\n(`]\s*)(?:(?:sudo|time)\s+)*'
    r'(?:cat|less|more|head|tail|sed|bat|nl)\b[^;&|\n]*?'
    r"""(?:^|[/\s'"=])skills/([A-Za-z0-9_.-]+)/SKILL\.md\b"""
)


def skill_name_from_read(tool_name: str, tool_input: dict) -> str | None:
    """Return the skill name if this tool call Reads a SKILL.md.

    Agents load skills three ways: the ``Skill`` tool (already tracked
    into ``_loaded_skills`` by the prereq hook), a plain ``Read`` of
    ``.claude/skills/<name>/SKILL.md`` (the catalog-driven path), and a
    shell read of the same file. Skill-declared exit gates must see all
    of them, or a skill loaded another way silently skips its own
    reviewers.

    The shell path is not hypothetical. In CI an agent `cat`-ed the
    amazon-ads SKILL.md, ran its whole audit procedure, and submitted --
    with none of the skill's gates bound, so ``set_task_result``
    accepted the report unchecked while the Stop hook (keyed on the
    report file) demanded a reviewer: two exits, two verdicts.
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
