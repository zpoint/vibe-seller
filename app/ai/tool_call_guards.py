"""Per-call refusals the PreToolUse hook applies to every tool.

One entry point so the hook (at its line budget) chains a single call:
a vibe-seller MCP call missing required arguments
(``mcp_args_guard``), any tool reaching into vibe-seller's own source
(``source_guard``), an execution-only tool called while planning, or a
shell command that calls the ads service around its tool.
"""

from urllib.parse import urlsplit

from app.ads_client import SERVICE_URL
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


def _service_hosts() -> tuple[str, ...]:
    netloc = urlsplit(SERVICE_URL).netloc
    if not netloc:
        return ()
    host, _, port = netloc.partition(':')
    tail = f':{port}' if port else ''
    aliases = {'127.0.0.1', 'localhost'}
    if host in aliases:
        return tuple(f'{h}{tail}' for h in aliases)
    return (netloc,)


_SERVICE_HOSTS = _service_hosts()

_SERVICE_DENY = (
    'Blocked: the ads service is reached only through '
    "`vibe_seller_ads_call`, which applies the store's binding and the "
    'planning rules. Calling it directly walks around both. If you are '
    'planning, write the call into the plan; if this store is not '
    'authorized, its ad data comes from the console.'
)


def ads_service_deny(tool_name: str, tool_input) -> str | None:
    """Refuse a shell command that talks to the ads service directly.

    Observed in CI: a planner refused `vibe_seller_ads_call` (planning)
    and the API skill (no store) found the service's port and `curl`-ed
    it, with no key and then a store NAME as the key -- the same bypass
    class as `cat`-ing a skill the routing refused.
    """
    if tool_name != 'Bash' or not isinstance(tool_input, dict):
        return None
    cmd = str(tool_input.get('command', ''))
    if any(h in cmd for h in _SERVICE_HOSTS):
        return _SERVICE_DENY
    return None


def tool_call_deny(session, tool_name: str, tool_input) -> str | None:
    """First refusal for this tool call, else ``None``."""
    return (
        missing_args_deny(tool_name, tool_input)
        or source_access_deny(tool_name, tool_input)
        or planning_deny(session, tool_name)
        or ads_service_deny(tool_name, tool_input)
    )
