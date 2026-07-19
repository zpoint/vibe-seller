"""Module-level helpers shared across claude_backend split modules."""

import json
import logging
import os
from pathlib import Path
import re
import sys

from sqlalchemy import func, select

from app.ai.bash_safety import (
    check_exec_review_status,
    check_review_status,
)
from app.ai.skill_gate_utils import find_skill_md
from app.config import WEB_BROWSER_SLUG
from app.database import async_session
from app.env_options import Options
from app.models.schedule import Schedule
from app.models.task import Task
from app.models.task_message import TaskMessage
from app.platform import prepend_to_path, venv_bin_dir
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


# A `browser-use` that always ERRORS — sits on the agent PATH just below
# the per-store wrapper. If the wrapper dir is empty (a failed/stale launch,
# or a boot wipe before the next task rewrites it), bare `browser-use` hits
# this guard and fails LOUDLY instead of falling through to the real binary
# in the venv — which would attach to the user's LOCAL Chrome (wrong,
# unisolated browser). See docs/ziniao-concurrency.md.
_GUARD_HEADER = '# Auto-generated browser-use guard (vibe-seller)'
_BROWSER_USE_GUARD = f"""#!/usr/bin/env bash
{_GUARD_HEADER} — do not edit.
echo "ERROR: no browser-use wrapper on PATH — the managed browser session" >&2
echo "is not ready. This is NOT a browser-use syntax issue, and you must" >&2
echo "NOT run raw browser-use or attach to a local Chrome. Retry the task;" >&2
echo "the wrapper (re)starts the browser session on demand." >&2
exit 1
"""


def _ensure_browser_use_guard(vibe_home: Path) -> Path | None:
    """Write (idempotently) the guard ``browser-use`` and return its dir."""
    try:
        guard_dir = vibe_home / 'bin' / '_guard'
        guard = guard_dir / 'browser-use'
        if not guard.is_file() or _GUARD_HEADER not in guard.read_text(
            errors='replace'
        ):
            guard_dir.mkdir(parents=True, exist_ok=True)
            guard.write_text(_BROWSER_USE_GUARD)
            os.chmod(guard, 0o755)
        return guard_dir
    except OSError as e:
        logger.warning('Could not write browser-use guard: %s', e)
        return None


def apply_agent_venv_path(
    env: dict,
    store_slug: str | None,
    *,
    vibe_home: Path = VIBE_SELLER_DIR,
    server_bin: Path | None = None,
) -> None:
    """Wire the task agent's ``PATH`` and ``VIRTUAL_ENV``.

    PATH priority (highest first):
      1. browser-use wrapper (``bin/<slug>``) — must win so every
         ``browser-use`` call goes through session/CDP injection. Store
         tasks use their per-store slug; no-store (orchestrator) tasks
         fall back to the shared ``bin/_web`` wrapper for the store-less
         web browser.
      2. shared agent venv (``~/.vibe-seller/.venv``) — the agent's
         ``python`` / ``pip``.
      3. server/install venv (``sys.executable``'s dir) — fallback for
         ``browser-use.exe`` and app deps.

    The agent's ``python``/``pip`` MUST be the shared agent venv, not the
    server venv. The server venv is built by ``uv pip install`` with no
    pip seeded (packaged installs), so an agent ``pip install X`` there
    fails and the agent falls back to a stray system Python — installing
    into a different interpreter than it runs (the Windows symptom:
    landing on ``...\\Programs\\Python\\Python313``). The shared venv is
    created once, reused across tasks, bootstrapped WITH pip, and is
    where skill deps land. Mirrors ``workspace_assistant.py``.

    Do NOT ``.resolve()`` the server bin: uv-created venvs symlink the
    interpreter to a base Python whose bin often ships its own
    ``browser-use`` that would shadow the wrapper.
    """
    if server_bin is None:
        server_bin = Path(sys.executable).parent
    # 3. server venv (lowest of ours) — browser-use.exe / app fallback.
    if server_bin.is_dir():
        prepend_to_path(env, server_bin)
    # 2. shared agent venv — the agent's python/pip (has pip; reused).
    agent_venv = vibe_home / '.venv'
    agent_venv_bin = venv_bin_dir(agent_venv)
    if agent_venv_bin.is_dir():
        prepend_to_path(env, agent_venv_bin)
        env['VIRTUAL_ENV'] = str(agent_venv)
    elif server_bin.is_dir():
        # Shared venv not ready (should not happen after ensure_init) —
        # fall back to the server venv as the active env.
        env['VIRTUAL_ENV'] = str(server_bin.parent)
    # 1b. Guard — a `browser-use` that ERRORS, just below the wrapper and
    #     ABOVE both venvs. If the wrapper is missing, bare `browser-use`
    #     hits this and fails loudly instead of falling through to the real
    #     binary → the user's local Chrome. See docs/ziniao-concurrency.md.
    guard_bin = _ensure_browser_use_guard(vibe_home)
    if guard_bin is not None:
        prepend_to_path(env, guard_bin)
    # 1a. browser-use wrapper — must sit ahead of the guard + both venvs.
    #     Store tasks use bin/<slug>; no-store (orchestrator) tasks fall
    #     back to the shared bin/_web wrapper. Only prepended if the dir
    #     exists; when absent the guard above catches bare `browser-use`.
    wrapper_bin = vibe_home / 'bin' / (store_slug or WEB_BROWSER_SLUG)
    if wrapper_bin.is_dir():
        prepend_to_path(env, wrapper_bin)


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


def check_review_status_for_stop(
    task_dir: Path | None, subagent_ran=None
) -> str | None:
    """Return a deny reason for Stop if the ads-audit reviewer
    hasn't run (or returned gaps); otherwise ``None``.

    Thin wrapper around ``bash_safety.check_review_status`` that
    handles the empty-task_dir case for the hook caller. Quiet
    no-op for non-ads tasks (no ``AD_AUDIT_*.md`` in the workspace).
    ``subagent_ran`` (False when the stream saw no review-subagent spawn
    this turn) makes an accepting verdict get rejected as self-written.
    See ``amazon-ads/references/reviewer-loop.md`` for the contract.
    """
    if task_dir is None:
        return None
    return check_review_status(task_dir, subagent_ran)


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
