"""Per-call refusals the PreToolUse hook applies to every tool.

One entry point so the hook (at its line budget) chains a single call:
a vibe-seller MCP call missing required arguments
(``mcp_args_guard``), any tool reaching into vibe-seller's own source
(``source_guard``), or an execution-only tool called while planning.
"""

from app.ai.mcp_args_guard import missing_args_deny
from app.ai.source_guard import source_access_deny

# Tools that DO the task. While planning, the agent decides what to do;
# these run when the plan executes. Observed in CI: a fanout planner
# called the live ads API eight times and filed an ad-task declaration
# before writing a plan, and overran its window doing so.
_EXECUTION_ONLY = frozenset({
    'mcp__vibe-seller__vibe_seller_ads_call',
    'mcp__vibe-seller__vibe_seller_declare_ad_task',
})

_PLANNING_DENY = (
    'Blocked: `{tool}` does the task, and this session is still planning. '
    'Planning decides WHAT to do — write this step into the plan; it runs '
    'when the plan executes.'
)


def planning_deny(session, tool_name: str) -> str | None:
    """Refuse an execution-only tool during a plan-mode session's planning."""
    planning = getattr(session, 'mode', '') == 'plan_then_execute' and not (
        getattr(session, '_executing', True)
    )
    if planning and tool_name in _EXECUTION_ONLY:
        return _PLANNING_DENY.format(tool=tool_name)
    return None


def tool_call_deny(session, tool_name: str, tool_input) -> str | None:
    """First refusal for this tool call, else ``None``."""
    return (
        missing_args_deny(tool_name, tool_input)
        or source_access_deny(tool_name, tool_input)
        or planning_deny(session, tool_name)
    )
