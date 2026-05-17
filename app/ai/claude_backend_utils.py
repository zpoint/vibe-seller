"""Module-level helpers shared across claude_backend split modules."""

import logging
from pathlib import Path
import re

from sqlalchemy import func, select

from app.env_options import Options
from app.models.task_message import TaskMessage
from app.workspace.manager import VIBE_SELLER_DIR

logger = logging.getLogger(__name__)

AGENT_DEBUG = Options.AGENT_DEBUG.get_bool()

# Graceful shutdown timeouts (seconds)
INTERRUPT_TIMEOUT = 5  # wait for graceful Result after interrupt
SIGNAL_TIMEOUT = 2  # between signal escalation steps
DRAIN_TIMEOUT = 2  # for _task to finish after force-kill

# Hook callback IDs for control protocol
TOOL_APPROVAL_CALLBACK = 'tool_approval'
AUTO_APPROVE_CALLBACK = 'auto_approve'
STOP_REFLECTION_CALLBACK = 'stop_reflection'

# Circuit breaker: stop agent after N identical consecutive tool calls
MAX_REPEAT_TOOL_CALLS = Options.MAX_REPEAT_TOOL_CALLS.get_int()


async def get_next_seq(db, task_id: str) -> int:
    """Get next sequence number for task messages."""
    result = await db.execute(
        select(func.max(TaskMessage.seq)).where(TaskMessage.task_id == task_id)
    )
    max_seq = result.scalar()
    return (max_seq + 1) if max_seq is not None else 0


def parse_wait_condition(result_text: str) -> dict | None:
    """Extract a wait-condition block from agent result text.

    Returns a normalised dict or None if no valid block is found.
    """
    match = re.search(
        r'```wait-condition\s*\n(.*?)\n```',
        result_text,
        re.DOTALL,
    )
    if not match:
        return None

    body = match.group(1)
    condition: dict = {}
    for line in body.strip().splitlines():
        if ':' in line:
            key, val = line.split(':', 1)
            condition[key.strip()] = val.strip()

    # Normalise keywords to a list.
    if 'keywords' in condition:
        condition['keywords'] = [
            k.strip() for k in condition['keywords'].split(',')
        ]
    else:
        condition['keywords'] = []

    condition.setdefault('check_strategy', 'manual')

    try:
        condition['max_wait_days'] = int(condition.get('max_wait_days', 30))
    except (ValueError, TypeError):
        condition['max_wait_days'] = 30

    try:
        condition['check_interval_hours'] = int(
            condition.get('check_interval_hours', 24)
        )
    except (ValueError, TypeError):
        condition['check_interval_hours'] = 24

    # reason is required.
    if not condition.get('reason'):
        return None

    return condition


# ── Skill prerequisite parsing ──────────────────────────

# Matches `requires: [a, b, "c"]` in a YAML frontmatter line.
# Deliberately minimal — only the inline-list form is supported.
# Same parser used by the PreToolUse hook to enforce ordering
# without inlining content on disk.
_REQUIRES_RE = re.compile(r'^requires:\s*\[([^\]]*)\]\s*$', re.MULTILINE)


def parse_skill_requires(skill_md_path: Path) -> list[str]:
    """Return the list of skill names this SKILL.md requires.

    Reads YAML frontmatter, looks for `requires: [a, b]`. Returns
    ``[]`` if the file is missing, has no frontmatter, or has no
    requires field.
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
    frontmatter = text[: end + 5]
    m = _REQUIRES_RE.search(frontmatter)
    if not m:
        return []
    return [
        item.strip().strip('"\'')
        for item in m.group(1).split(',')
        if item.strip()
    ]


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


def check_skill_prereqs(
    skill_name: str,
    workspace_dir: Path,
    loaded_skills: set[str],
    task_id_prefix: str = '',
) -> str | None:
    """Return a deny-reason if loading ``skill_name`` would skip a
    prerequisite the agent hasn't loaded yet; ``None`` otherwise.

    Read-only: does not mutate ``loaded_skills``. The hook handler
    is responsible for tracking successful loads. Lives here (not in
    the hook file) to keep ``claude_backend_hooks.py`` under the
    800-line pre-commit limit.
    """
    if not skill_name:
        return None
    skill_md = find_skill_md(workspace_dir, skill_name)
    if skill_md is None:
        logger.debug(
            'Skill prereq: %s no SKILL.md for %r (ws=%s)',
            task_id_prefix,
            skill_name,
            workspace_dir,
        )
        return None
    requires = parse_skill_requires(skill_md)
    logger.debug(
        'Skill prereq: %s checking %r requires=%s loaded=%s',
        task_id_prefix,
        skill_name,
        requires,
        sorted(loaded_skills),
    )
    missing = [r for r in requires if r not in loaded_skills]
    if not missing:
        return None
    first = missing[0]
    return (
        f"Skill '{skill_name}' requires '{first}' to be loaded"
        f' first. Call the Skill tool with skill={first!r}, then'
        f' retry skill={skill_name!r}.'
    )
