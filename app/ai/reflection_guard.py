"""Bounds on the post-result reflection phase.

Reflection runs after the Stop hook has saved the task's result: the
agent records what THIS task taught it, then ends. The task stays
RUNNING until it does, so reflection that starts new work holds a
finished task open. Observed in CI: a notes-writing task's reflection
ran a store's sign-in script to "verify" a skill the task never used,
grepped every skill, and began editing built-ins -- seven minutes past
an accepted result.

Two contracts, enforced at PreToolUse:

- Reflection records; it does not act. Commands that run programs
  (browser, scripts, network) and vibe-seller tools with side effects
  are refused -- an unverified doubt goes into the notes as unverified.
- Reflection is time-boxed. Past ``REFLECTION_BUDGET_S`` every tool is
  refused, so the turn ends and the task finishes.
"""

import re
import time

REFLECTION_BUDGET_S = 240

# Reading and writing knowledge is what reflection is for.
_ALLOWED_MCP = frozenset(
    f'mcp__vibe-seller__vibe_seller_{name}'
    for name in (
        'write_workspace_file',
        'save_skill',
        'list_skills',
        'list_stores',
        'list_tasks',
        'list_cron_jobs',
        'get_schedule_state',
        'set_schedule_state',
    )
)

# A program run in command position: the browser, an interpreter, a
# fetcher, or a script path executed directly.
_RUNS_PROGRAM_RE = re.compile(
    r'(?:^|[;&|\n(`]\s*)(?:(?:sudo|time|env)\s+)*'
    r'(?:browser-use|python3?|uv|uvx|node|npx|curl|wget|bash|sh'
    r'|\S*/[\w.-]+\.(?:py|sh))\b'
)

_ACT_DENY = (
    'Blocked: reflection records what this task already did; it does not '
    'run new checks. The result is saved. If something is unverified, note '
    'it as unverified (for a built-in skill, under `## Skill follow-ups` in '
    'the store notes) and end your turn.'
)

_BUDGET_DENY = (
    'Blocked: reflection time is up and the result is already saved. '
    'End your turn now without further tool calls.'
)


def begin_reflection(session) -> None:
    session._reflection_active = True
    session._reflection_started_at = time.monotonic()


def end_reflection(session) -> None:
    session._reflection_active = False


def reflection_deny(session, tool_name: str, tool_input) -> str | None:
    """Refusal for a tool call during reflection, else ``None``."""
    if not getattr(session, '_reflection_active', False):
        return None
    started = getattr(session, '_reflection_started_at', None)
    if started is not None and (
        time.monotonic() - started > REFLECTION_BUDGET_S
    ):
        return _BUDGET_DENY
    if tool_name.startswith('mcp__vibe-seller__'):
        return None if tool_name in _ALLOWED_MCP else _ACT_DENY
    if tool_name == 'Bash' and isinstance(tool_input, dict):
        if _RUNS_PROGRAM_RE.search(str(tool_input.get('command', ''))):
            return _ACT_DENY
    return None
