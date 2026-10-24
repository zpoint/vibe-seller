"""Per-call refusals the PreToolUse hook applies to every tool.

One entry point so the hook (at its line budget) chains a single call:
a vibe-seller MCP call missing required arguments
(``mcp_args_guard``), any tool reaching into vibe-seller's own source
(``source_guard``), an execution-only tool called while planning, a
shell command that calls the ads service around its tool, new work
started during post-result reflection (``reflection_guard``), or a tool
search for a tool that is already loaded.
"""

import os
from urllib.parse import urlsplit

from app.ads_client import SERVICE_URL
from app.ai.mcp_args_guard import missing_args_deny
from app.ai.reflection_guard import reflection_deny
from app.ai.source_guard import source_access_deny
from app.mcp_tool_schemas import TOOLS

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


_FINISHING = frozenset({
    'mcp__vibe-seller__vibe_seller_set_task_result',
    'mcp__vibe-seller__vibe_seller_set_task_error',
})


def subagent_finish_deny(session, tool_name: str, tool_use_id) -> str | None:
    """A subagent may not submit or fail the task it was spawned to help.

    Observed in CI: a reviewer subagent called set_task_result with its
    own "Status: ok …" text as the task's result. The stream records
    which tool_use ids a subagent made (their events carry a parent id).
    """
    made_by_subagent = tool_use_id in getattr(
        session, '_subagent_tool_use_ids', ()
    )
    if tool_name in _FINISHING and made_by_subagent:
        return (
            f"Blocked: `{tool_name}` is the main agent's call. You are a "
            'subagent: write your output (e.g. the review verdict file) '
            'and return; the main agent submits the task.'
        )
    return None


# Tools that are always loaded, each with the query stems that mean
# "looking for it". ToolSearch lists only DEFERRED tools, so a search
# for one of these always comes back empty -- and a model reads empty as
# "missing". In CI one searched six times for the set_task_result it
# already had; another searched `ask.*question|AskUser`, concluded
# AskUserQuestion "doesn't exist in this runtime", asked in prose, and
# parked its task in WAITING (then wrote that falsehood into the notes).
#
# Built-ins are matched only by distinctive stems: "read" or "write"
# would catch searches meant for deferred tools (`read email`).
_ALWAYS_LOADED = sorted(
    t['name']
    for t in TOOLS
    if (t.get('_meta') or {}).get('anthropic/alwaysLoad') is True
)
_LOADED_STEMS: dict[str, tuple[str, ...]] = {
    f'mcp__vibe-seller__{n}': (
        n.removeprefix('vibe_seller_'),
        os.path.commonprefix(_ALWAYS_LOADED)
        .removeprefix('vibe_seller_')
        .rstrip('_'),
    )
    for n in _ALWAYS_LOADED
}
_LOADED_STEMS['AskUserQuestion'] = ('askuser', 'ask_user')


def loaded_tool_search_deny(tool_name: str, tool_input) -> str | None:
    """Refuse a ToolSearch for a tool that is already loaded."""
    if tool_name != 'ToolSearch' or not isinstance(tool_input, dict):
        return None
    query = str(tool_input.get('query', '')).lower()
    hits = [
        name
        for name, stems in _LOADED_STEMS.items()
        if any(stem and stem in query for stem in stems)
    ]
    if not hits:
        return None
    names = ', '.join(f'`{n}`' for n in hits)
    verb = 'is' if len(hits) == 1 else 'are'
    return (
        f'Blocked: {names} {verb} already loaded; tool search lists only '
        'tools that are not, so it will never show it. Call it directly.'
    )


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
        or reflection_deny(session, tool_name, tool_input)
        or loaded_tool_search_deny(tool_name, tool_input)
    )
