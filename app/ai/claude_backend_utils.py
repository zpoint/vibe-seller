"""Module-level helpers shared across claude_backend split modules."""

import json
import logging
import os
from pathlib import Path
import re

from sqlalchemy import func, select

from app.ai.bash_safety import (
    check_exec_review_status,
    check_review_status,
)
from app.database import async_session
from app.env_options import Options
from app.models.schedule import Schedule
from app.models.task import Task
from app.models.task_message import TaskMessage
from app.workspace.manager import VIBE_SELLER_DIR

logger = logging.getLogger(__name__)

AGENT_DEBUG = Options.AGENT_DEBUG.get_bool()


def resolve_claude_binary() -> str:
    """Return the Claude Code binary path the daemon should spawn.

    Prefers the project-local install at
    ``<VIBE_SELLER_DIR>/node_modules/.bin/claude`` (analogous to the
    Python venv at ``<VIBE_SELLER_DIR>/.venv/``); falls back to
    ``claude`` on ``PATH`` when the project-local install is absent.

    Pinning the version vibe-seller uses to whatever ``install.sh``
    installs, instead of trusting whatever the user has globally,
    insulates the daemon from upstream regressions (e.g. Claude Code
    2.1.154+ shipped a request-body change that strict Anthropic-
    compatible providers reject with HTTP 400).
    """
    local = VIBE_SELLER_DIR / 'node_modules' / '.bin' / 'claude'
    if local.is_file() and os.access(local, os.X_OK):
        return str(local)
    return 'claude'


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


def get_open_tasklist_items(task_id: str) -> list[tuple[str, str]]:
    """Return [(id, subject), ...] for pending/in_progress TaskList
    entries belonging to this task.

    Claude Code stores each TaskList item as a JSON file at
    ``~/.claude/tasks/<task_list_id>/<n>.json``. We pin
    ``CLAUDE_CODE_TASK_LIST_ID=vibe-<task_id_short>`` in the agent
    env (see ``claude_backend.py``) so the directory path is
    predictable from the task id.

    Used by the Stop-hook completion gate to deny stop while the
    agent still has open work. Returns empty list when the
    directory is absent (agent never created any TaskList items —
    no gate to enforce; let stop proceed).
    """
    task_list_id = f'vibe-{task_id[:8]}'
    tasks_dir = Path.home() / '.claude' / 'tasks' / task_list_id
    if not tasks_dir.is_dir():
        return []
    open_items: list[tuple[str, str]] = []
    for path in sorted(tasks_dir.glob('*.json')):
        if path.name.startswith('.'):
            continue
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if data.get('status', '') in ('pending', 'in_progress'):
            open_items.append((
                str(data.get('id', path.stem)),
                str(data.get('subject', '')),
            ))
    return open_items


def build_tasklist_open_reason(open_items: list[tuple[str, str]]) -> str:
    """Build the deny-stop reason text from open TaskList items.

    Lives here so the Stop-hook call site stays small under the
    800-line file limit. ``open_items`` is the output of
    ``get_open_tasklist_items``.
    """
    lst = '\n'.join(f'- [#{i}] {s}' for i, s in open_items[:20])
    more = f'\n…and {len(open_items) - 20} more' if len(open_items) > 20 else ''
    return (
        f'TaskList still has {len(open_items)} open item(s):\n'
        f'{lst}{more}\n\n'
        'Continue executing the next pending item. After every'
        ' action: TaskUpdate to "completed". Phase 4 is only done'
        ' when TaskList shows zero open items. Re-call TaskList'
        ' NOW to confirm current state, then pick the lowest-ID'
        ' open item.'
    )


_FANOUT_FORBIDDEN_PATTERNS = (
    ('vibe_seller_create_task', 'calls the sub-task MCP tool'),
    ('parent_task_id', 'designs a parent/sub-task hierarchy'),
)


async def validate_fanout_plan_text(task_id: str, plan_text: str) -> str | None:
    """Return a deny reason if *plan_text* contains fanout-illegal
    patterns for a plan-only fanout-schedule Task, else None.

    Only runs for ``Task.is_plan_only=True`` whose owning
    ``Schedule.phase_mode='fanout'``. Other cases (single-mode
    schedules, standalone interactive plan-mode tasks) are not
    subject to this check — orchestration is legitimate for them.

    Lives here (not as an AgentSession method) to keep the hook
    module under the 800-line file limit.
    """
    try:
        async with async_session() as db:
            task = await db.get(Task, task_id)
            if not task or not task.is_plan_only or not task.schedule_id:
                return None
            sched = await db.get(Schedule, task.schedule_id)
            if not sched or sched.phase_mode != 'fanout':
                return None
    except Exception:
        logger.debug(
            'Fanout-plan validator could not load context for %s',
            task_id,
            exc_info=True,
        )
        return None

    haystack = (plan_text or '').lower()
    for needle, description in _FANOUT_FORBIDDEN_PATTERNS:
        if needle.lower() in haystack:
            return (
                f'the plan {description}'
                f' (found {needle!r}), but this schedule is a'
                ' fanout schedule — the scheduler already creates'
                ' one per-store Task per fire and runs the plan'
                ' once per store. Remove the orchestrator step'
                ' and describe what a single store-bound agent'
                ' should do with the per-store L3 catalog.'
            )
    return None


def check_review_status_for_stop(task_dir: Path | None) -> str | None:
    """Return a deny reason for Stop if the ads-audit reviewer
    hasn't run (or returned gaps); otherwise ``None``.

    Thin wrapper around ``bash_safety.check_review_status`` that
    handles the empty-task_dir case for the hook caller. Quiet
    no-op for non-ads tasks (no ``AD_AUDIT_*.md`` in the workspace).
    See ``amazon-ads/references/reviewer-loop.md`` for the contract.
    """
    if task_dir is None:
        return None
    return check_review_status(task_dir)


def check_exec_review_status_for_stop(
    task_dir: Path | None,
) -> str | None:
    """Return a deny reason for Stop if the ads-execution reviewer
    hasn't returned ok; otherwise ``None``.

    Thin wrapper around ``bash_safety.check_exec_review_status``.
    Quiet no-op when ``EXECUTION_LOG.md`` is absent in the workspace
    (task is not in execution mode). See
    ``amazon-ads/references/reviewer-loop.md § Execution-review mode``.
    """
    if task_dir is None:
        return None
    return check_exec_review_status(task_dir)
