"""Per-call refusals the PreToolUse hook applies to every tool.

One entry point so the hook (at its line budget) chains a single call:
a subagent submitting or failing the task, a vibe-seller MCP call
missing required arguments (``mcp_args_guard``), any tool reaching into
vibe-seller's own source (``source_guard``), an execution-only tool
called while planning, and a shell command that calls the ads service
around its tool.
"""

import re
from urllib.parse import urlsplit

from app.ads_client import SERVICE_URL
from app.ai.mcp_args_guard import missing_args_deny
from app.ai.shell_patterns import COMMAND_PREFIX
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

# The only Bash shapes that REACH the service over the network: a
# network client in command position (its arguments carry the URL/host),
# or an interpreter running inline CODE that names the host. Naming the
# host some other way — `grep`ping notes that mention the URL, an echo,
# a plain file path — is not a request and is left alone: that was a
# substring match over the whole command, which refused reading a store
# note that happened to quote the endpoint. False negatives stay fine
# (an obscure socket call slips through), false positives are the cost
# this narrowing pays to stop blocking ordinary text work.
_NET_CLIENT_INVOCATION = re.compile(
    COMMAND_PREFIX
    + r'(?:curl|wget|http|https|httpie|aria2c|nc|ncat|netcat|telnet|socat)'
    r'\b([^;&|\n]*)'
)
# ``python -c "... host ..."`` / ``node -e`` — inline code that can open
# a socket; a script invoked by FILENAME is never inspected here.
_INLINE_CODE_INVOCATION = re.compile(
    COMMAND_PREFIX
    + r'(?:python\d*|pypy\d*|node|deno|ruby|perl|php|groovy)\b'
    + r'[^;&|\n]*?(?:\s-c\b|\s-e\b|--eval\b|--command\b)[^;&|\n]*'
)

_SERVICE_DENY = (
    'Blocked: the ads service is reached only through '
    "`vibe_seller_ads_call`, which applies the store's binding and the "
    'planning rules. Calling it directly walks around both. If you are '
    'planning, write the call into the plan; if this store is not '
    'authorized, its ad data comes from the console.'
)


def ads_service_deny(tool_name: str, tool_input) -> str | None:
    """Refuse a shell command that makes a NETWORK request to the service.

    Observed in CI: a planner refused `vibe_seller_ads_call` (planning)
    and the API skill (no store) found the service's port and `curl`-ed
    it, with no key and then a store NAME as the key -- the same bypass
    class as `cat`-ing a skill the routing refused. The guard fires on
    the request, not on the host name appearing in the command text.

    A network client is checked per invocation (its arguments stop at
    ``;``/``|``/``&``/newline). Inline interpreter code is checked over
    the whole command: quotes hide shell separators from a string
    match, and an inline-code run that names the endpoint anywhere is
    the act; a FILE-run script (`python parse.py`) is never inspected
    here, per the convention of these guards.
    """
    if tool_name != 'Bash' or not isinstance(tool_input, dict):
        return None
    cmd = str(tool_input.get('command', ''))
    for m in _NET_CLIENT_INVOCATION.finditer(cmd):
        if any(h in m.group(1) for h in _SERVICE_HOSTS):
            return _SERVICE_DENY
    if _INLINE_CODE_INVOCATION.search(cmd):
        if any(h in cmd for h in _SERVICE_HOSTS):
            return _SERVICE_DENY
    return None


_FINISHING = frozenset({
    'mcp__vibe-seller__vibe_seller_set_task_result',
    'mcp__vibe-seller__vibe_seller_set_task_error',
})


def subagent_finish_deny(session, tool_name: str, tool_use_id) -> str | None:
    """A subagent may not submit or fail the task it was spawned to help.

    Observed in CI: a background subagent called set_task_result with its
    own "Status: ok …" text as the task's result. The stream records
    which tool_use ids a subagent made (their events carry a parent id).
    """
    made_by_subagent = tool_use_id in getattr(
        session, '_subagent_tool_use_ids', ()
    )
    if tool_name in _FINISHING and made_by_subagent:
        return (
            f"Blocked: `{tool_name}` is the main agent's call. You are a "
            'subagent: write your output to a file and return; the main '
            'agent submits the task.'
        )
    return None


def tool_call_deny(session, hook_input: dict) -> str | None:
    """First refusal for this PreToolUse call, else ``None``.

    ``hook_input`` is the hook callback's input: ``tool_name``,
    ``tool_input`` and the CLI's ``tool_use_id``.
    """
    tool_name = hook_input.get('tool_name', '')
    tool_input = hook_input.get('tool_input', {})
    return (
        subagent_finish_deny(session, tool_name, hook_input.get('tool_use_id'))
        or missing_args_deny(tool_name, tool_input)
        or source_access_deny(tool_name, tool_input)
        or planning_deny(session, tool_name)
        or ads_service_deny(tool_name, tool_input)
    )
