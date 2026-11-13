"""Module-level helpers shared across claude_backend split modules."""

import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sys
import uuid

from sqlalchemy import func, select

from app.ai.skill_gate_utils import find_skill_md
from app.config import WEB_BROWSER_SLUG
from app.env_options import Options
from app.models.task_message import TaskMessage
from app.platform import IS_WINDOWS, prepend_to_path, venv_bin_dir
from app.workspace.manager import VIBE_SELLER_DIR

logger = logging.getLogger(__name__)

AGENT_DEBUG = Options.AGENT_DEBUG.get_bool()


def _is_spawnable(path: Path) -> bool:
    """True when this OS can hand ``path`` straight to CreateProcess/exec.

    ``os.access(..., X_OK)`` is meaningless on Windows — it answers True
    for any existing file. That is precisely how npm's POSIX shell shim
    (``.bin/claude``, no extension, ``#!/bin/sh`` inside) got selected
    and then died in ``CreateProcess`` with ``WinError 2``. On Windows
    executability IS the extension, so the candidate list carries it and
    existence is the only check left to make.
    """
    if IS_WINDOWS:
        return path.is_file()
    return path.is_file() and os.access(path, os.X_OK)


def _claude_candidates() -> tuple[Path, ...]:
    """Project-local claude paths to try, most preferred first."""
    node_modules = VIBE_SELLER_DIR / 'node_modules'
    if IS_WINDOWS:
        # The npm package's entry point is a NATIVE executable on every
        # platform (``"bin": {"claude": "bin/claude.exe"}``), so target
        # it directly instead of one of npm's generated shims. Spawning
        # ``claude.cmd`` makes CreateProcess route through ``cmd.exe``,
        # which (a) caps the ENTIRE command line at 8191 chars — the
        # assembled 10-20KB system prompt died there instantly with
        # ``命令行太长`` — and (b) re-parses every argument under cmd
        # quoting rules, so a path holding ``&`` or ``^`` (``--add-dir``)
        # breaks. The ``.exe`` goes straight to CreateProcess: 32767
        # chars, no shell, no quoting layer. The ``.cmd`` stays as a
        # fallback in case the package layout ever changes.
        return (
            node_modules
            / '@anthropic-ai'
            / 'claude-code'
            / 'bin'
            / 'claude.exe',
            node_modules / '.bin' / 'claude.cmd',
        )
    return (node_modules / '.bin' / 'claude',)


def resolve_claude_binary() -> str:
    """Return the Claude Code binary path the daemon should spawn.

    Prefers the project-local install under
    ``<VIBE_SELLER_DIR>/node_modules/`` (analogous to the Python venv at
    ``<VIBE_SELLER_DIR>/.venv/``); falls back to ``claude`` on ``PATH``
    when the project-local install is absent.

    Owning the copy vibe-seller uses, instead of trusting whatever the
    user has globally, means ``install.sh`` and ``vibe-seller upgrade``
    decide its version (see ``app.cli._claude_cli_upgrade_cmd``).

    The contract is "a path THIS OS can execute" — see
    ``_is_spawnable`` and ``_claude_candidates`` for why that differs
    per platform. The packaged Windows installer bundles its own
    ``claude.exe`` and puts that dir on ``PATH``
    (``installer/windows/README.md``), so it resolves via the ``PATH``
    fallback and never sees a shim.
    """
    for cand in _claude_candidates():
        if _is_spawnable(cand):
            return str(cand)
    found = shutil.which('claude')
    if found:
        # On Windows PATHEXT makes this find claude.exe / claude.cmd.
        return found
    return 'claude'


SYSTEM_PROMPT_FILENAME = '.system-prompt.md'


def system_prompt_file(task_id: str, task_dir: Path | None) -> Path:
    """Where a session's system prompt is written for claude to read.

    A task's own dir when it has one. A session without one (the
    workspace assistant) would otherwise land in the git-tracked
    workspace root, so it goes under ``data/`` — a runtime dir the
    workspace ``.gitignore`` already excludes.
    """
    if task_dir is not None:
        return task_dir / SYSTEM_PROMPT_FILENAME
    return VIBE_SELLER_DIR / 'data' / 'system-prompts' / f'{task_id}.md'


def append_system_prompt(
    cmd: list[str],
    system_prompt: str,
    task_id: str,
    task_dir: Path | None,
) -> None:
    """Hand the assembled system prompt to claude by file, never inline.

    The command line has a hard cap on Windows — 32767 chars for the
    whole of it through ``CreateProcess``, 8191 through a ``cmd.exe``
    batch shim — and the system prompt is the one argument whose size we
    do not control: base prompt, store context and, for a planned run,
    the whole plan. Inline, a schedule whose plan grew past ~7K chars
    stopped spawning at all (``[WinError 206] The filename or extension
    is too long``, zero messages) while the same schedule ran fine on
    macOS. ``--append-system-prompt-file`` keeps the command line the
    same size whatever the prompt holds, so there is no length that
    works on one OS and not the other.

    The file goes where ``system_prompt_file`` says — a task's own
    (gitignored, per-task, wiped-on-retry) dir, never a predictable name
    in a shared temp dir. A write failure propagates: falling back to
    the inline form would bring back the cap this exists to remove.
    An empty prompt adds nothing.
    """
    prompt_file = write_system_prompt_file(system_prompt, task_id, task_dir)
    if prompt_file is not None:
        cmd.extend(['--append-system-prompt-file', str(prompt_file)])


def write_system_prompt_file(
    system_prompt: str, task_id: str, task_dir: Path | None
) -> Path | None:
    """Write the system-prompt file; return its path (None when empty).

    Split out of ``append_system_prompt`` so the write can ride the
    ONE launch-artifact thread hop (:func:`build_launch_artifacts`)
    while the cmd flag stays a pure extension of the caller's argv.
    """
    if not system_prompt.strip():
        return None
    prompt_file = system_prompt_file(task_id, task_dir)
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(system_prompt, encoding='utf-8')
    return prompt_file


def mcp_launch_flags(
    *,
    load_global: bool,
    session_config: Path | None,
    project_config: Path | None = None,
) -> list[str]:
    """The MCP flags for one launch — ONE decision, both launches.

    A profile that opts OUT of global MCP servers runs strict on its
    own config file (a task's project ``.mcp.json``, or the assistant's
    per-session restricted-skill-tools file). A profile that opts IN
    keeps its global servers: the session file still rides along for
    the assistant (it is not auto-discovered from cwd), but WITHOUT
    ``--strict-mcp-config``; a task needs no flags at all, because its
    project ``.mcp.json`` IS auto-discovered from its cwd. Callers pass
    ``ProfileManager.get_load_global_mcp(profile_id)`` — the flag
    composition lives here, once.
    """
    if not load_global:
        cfg = session_config or project_config
        if cfg is None:
            return []
        return ['--mcp-config', str(cfg), '--strict-mcp-config']
    if session_config is not None and session_config != project_config:
        return ['--mcp-config', str(session_config)]
    return []


def write_mcp_config_file(
    mcp_config: dict, task_id: str, task_dir: Path | None
) -> Path:
    """Write the session's ``--mcp-config`` payload beside its prompt.

    Used by the workspace assistant, which gets its OWN restricted
    MCP registration — a per-session file, never the shared
    ``.mcp.json`` at the workspace root (store-less tasks write that
    with their full task toolset; two writers would race).
    """
    path = system_prompt_file(task_id, task_dir).with_suffix('.mcp.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mcp_config), encoding='utf-8')
    return path


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

# The raw console scripts the browser-use / browser-harness packages
# drop into the venv bins — which the agent PATH also carries. Each one
# reaches the real binary (same CLI or its MCP server), so `bu <<'PY'`
# would skip the wrapper AND the guard and attach to the user's LOCAL
# Chrome: the exact hole the guard exists for. The guard dir sits below
# the store wrapper and above both venvs on PATH, so erroring shims here
# intercept every alias while a real `browser-use` wrapper still wins.
_GUARD_ALIAS_NAMES = (
    'bu',
    'browser',
    'browseruse',
    'browser-use-tui',
    'browser-harness',
    'browser-harness-mcp',
)


def _alias_guard(name: str) -> str:
    return f"""#!/usr/bin/env bash
{_GUARD_HEADER} — do not edit.
echo "ERROR: '{name}' is a raw browser-use alias and is blocked here." >&2
echo "Use 'browser-use' instead — that is the managed store wrapper;" >&2
echo "the raw binary would attach to the user's own Chrome." >&2
exit 1
"""


def _ensure_browser_use_guard(vibe_home: Path) -> Path | None:
    """Write (idempotently) the guard ``browser-use`` and its alias shims.

    Header-checked like the original single guard: a pre-existing guard
    dir (no alias shims yet, or a foreign file) is brought up to date on
    the next call. Returns the guard dir.
    """
    try:
        guard_dir = vibe_home / 'bin' / '_guard'
        guard_dir.mkdir(parents=True, exist_ok=True)
        scripts = {'browser-use': _BROWSER_USE_GUARD}
        scripts.update({
            name: _alias_guard(name) for name in _GUARD_ALIAS_NAMES
        })
        for name, text in scripts.items():
            path = guard_dir / name
            if not path.is_file() or _GUARD_HEADER not in path.read_text(
                errors='replace'
            ):
                path.write_text(text)
                os.chmod(path, 0o755)
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


def permission_mode_for_agent(agent_mode: str) -> str:
    """Translate the agent's logical mode to Claude Code's
    ``--permission-mode`` CLI flag value.

    - ``plan_then_execute`` → ``plan`` (agent starts in plan mode so
      ExitPlanMode is valid; we transition to bypass via SetMode on
      approval).
    - Anything else (``execute``, ``auto``, ...) → ``bypassPermissions``.

    This is a hot rule: break the ``plan_then_execute`` branch and
    plan-only Tasks can't call ExitPlanMode (Claude Code returns
    ``"You are not in plan mode"``). A unit test pins the mapping.
    """
    return 'plan' if agent_mode == 'plan_then_execute' else 'bypassPermissions'


# Graceful shutdown timeouts (seconds)
INTERRUPT_TIMEOUT = 5  # wait for graceful Result after interrupt
SIGNAL_TIMEOUT = 2  # between signal escalation steps
DRAIN_TIMEOUT = 2  # for _task to finish after force-kill

# Hook callback IDs for control protocol
TOOL_APPROVAL_CALLBACK = 'tool_approval'


def sdk_initialize_request(mode: str) -> dict:
    """The SDK ``initialize`` control request registering this launch's
    hooks.

    One builder for both hook sets (the CLI evaluates matchers in
    order, first match wins):

    * ``plan_then_execute`` — ExitPlanMode + AskUserQuestion go to
      interactive approval (``tool_approval``); everything else is
      auto-approved.
    * ``auto`` / ``execute`` — AskUserQuestion goes to the interactive
      path; everything else is auto-approved with the circuit breaker
      (no ExitPlanMode hook).

    Every session gets the Stop hook: it refuses to end a turn whose own
    work is still open (see ``claude_backend_hooks``).
    """
    if mode == 'plan_then_execute':
        interactive = '^(ExitPlanMode|AskUserQuestion)$'
        everything_else = '^(?!ExitPlanMode$|AskUserQuestion$).*'
    else:
        interactive = '^AskUserQuestion$'
        everything_else = '^(?!AskUserQuestion$).*'
    hooks = {
        'PreToolUse': [
            {
                'matcher': interactive,
                'hookCallbackIds': [TOOL_APPROVAL_CALLBACK],
            },
            {
                'matcher': everything_else,
                'hookCallbackIds': [AUTO_APPROVE_CALLBACK],
            },
        ],
        'Stop': [{'hookCallbackIds': [STOP_CALLBACK]}],
    }
    return {
        'type': 'control_request',
        'request_id': str(uuid.uuid4()),
        'request': {'subtype': 'initialize', 'hooks': hooks},
    }


AUTO_APPROVE_CALLBACK = 'auto_approve'
STOP_CALLBACK = 'stop'

# Circuit breaker: stop an agent after N identical consecutive tool
# calls THAT ANSWERED IDENTICALLY TOO. A loop is the call AND its
# result: six polls whose answer keeps changing are progress (a report
# generating, a sync advancing), and killing them punished the one
# honest way to wait. Six identical answers to one identical call say
# the agent is repeating itself for nothing — that is the loop.
MAX_REPEAT_TOOL_CALLS = Options.MAX_REPEAT_TOOL_CALLS.get_int()


def check_tool_loop(
    recent_calls: list[dict],
    tool_use_id: str | None,
    tool_name: str,
    tool_input: dict,
) -> bool:
    """True if the agent is stuck in a degenerate tool loop.

    Appends this call to *recent_calls* (the session's rolling window,
    mutated in place; :func:`record_tool_result` fills each entry's
    answer when the stream carries it) and reports whether the last
    ``MAX_REPEAT_TOOL_CALLS`` calls are identical in name+input AND
    every one already answered said the same thing. An entry with no
    result yet (this call, or a still-running sibling) is evidence of
    nothing: the breaker waits for answers rather than assuming them.
    """
    sig = f'{tool_name}:{json.dumps(tool_input, sort_keys=True)}'
    recent_calls.append({'id': tool_use_id, 'sig': sig, 'result': None})
    if len(recent_calls) > MAX_REPEAT_TOOL_CALLS:
        del recent_calls[:-MAX_REPEAT_TOOL_CALLS]
    if len(recent_calls) < MAX_REPEAT_TOOL_CALLS:
        return False
    first = recent_calls[0]['sig']
    if any(c['sig'] != first for c in recent_calls):
        return False
    answered = [c['result'] for c in recent_calls[:-1]]
    if any(r is None for r in answered):
        return False
    return len(set(answered)) == 1


def record_tool_result(
    recent_calls: list[dict], tool_use_id: str | None, result_text: str
) -> None:
    """Fill in the answer a tool call got, keyed by ``tool_use_id``.

    Stores a digest, not the bytes — the window only ever compares
    equality. An id the window never saw (already trimmed, or a call
    this session's breaker does not track) is ignored: no evidence,
    no kill.
    """
    if not tool_use_id:
        return
    digest = hashlib.sha256(
        (result_text or '').encode('utf-8', 'replace')
    ).hexdigest()
    for entry in reversed(recent_calls):
        if entry['id'] == tool_use_id and entry['result'] is None:
            entry['result'] = digest
            return


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
    """A hint naming the prerequisites of ``skill_name`` that this
    session has not loaded yet; ``None`` otherwise. Advice only.

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
    names = ', '.join(missing)
    return (
        f"Skill '{skill_name}' builds on {names}; load "
        f'{"it" if len(missing) == 1 else "them"} with the Skill tool too.'
    )


# ATTEMPT bound on open-work re-drives per turn. Past this the
# hold FAILS OPEN: the stream banner-marks the result and the
# Stop hook stands down so the CLI exits cleanly (never a closed
# approval channel with a live deny).
#
# Attempts are the cheap half of the bound and say nothing about cost —
# five of these at 100-150s/turn is 500-750s, more clock than a caller
# gives a whole run. The wall-clock half lives in
# ``app/ai/turn_hold_budget.py``; ``AgentSession._hold_exhausted``
# is the ONE predicate that reads both. Do not compare against this
# constant directly — that is how the four fail-open sites drifted apart.
TURN_HOLD_MAX = 5
