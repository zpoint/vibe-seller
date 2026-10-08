"""Which ads skill a task gets.

A store bound to an ads service does its advertising over an API; an
unbound one still drives the seller console in a browser. The two need
different instructions, and shipping both into one task would let an
agent pick — so the choice is made here, from the store's binding state,
before the agent sees anything.

Both skills are copied only when a task genuinely spans both kinds of
store. That is a real case (an all-stores run mid-migration) and the
honest answer there is "both", but it is also a reason to prefer
store-scoped sub-tasks: with both present, the union of their exit gates
applies, and the browser skill's gates ask an API-path result to prove it
read a page it never opened.
"""

from __future__ import annotations

from collections.abc import Iterable
import re

#: The browser skill, for stores with no ads service binding.
BROWSER_SKILL = 'amazon-ads'
#: The API skill, pulled from the bound service.
API_SKILL = 'amazon-ads-api'


#: task id -> skills routed away from it, recorded as its workspace is
#: prepared. Keyed by task so a retried session keeps the routing.
_excluded: dict[str, frozenset[str]] = {}


def remember(task_id: str, skills: Iterable[str]) -> None:
    _excluded[task_id] = frozenset(skills)


def refusal_for(task_id: str, tool: str, tool_input: dict) -> str | None:
    """Why this tool call may not load a skill routed away from the task.

    Reached by name through the Skill tool, or by reading any file in the
    skill's folder. None when the call is not such a load.
    """
    barred = _excluded.get(task_id, frozenset())
    if tool == 'Skill':
        name = tool_input.get('skill', '')
    elif tool == 'Read':
        m = _SKILL_DIR.search(str(tool_input.get('file_path', '')))
        name = m.group(1) if m else ''
    else:
        return None
    return refusal(name) if name in barred else None


_SKILL_DIR = re.compile(r'(?:^|/)skills/([^/]+)/')


def refusal(skill: str) -> str:
    """Why a task may not load *skill*, and what to load instead.

    Leaving a skill out of the task's workspace is not enough on its own:
    the agent can still find the shared copy by name. So a load of an
    excluded skill is refused at the moment it is attempted, with the
    store's actual path named.
    """
    if skill == API_SKILL:
        return (
            f'`{API_SKILL}` is for stores authorized with the ads service, '
            f"and this task's store is not. Use the `{BROWSER_SKILL}` "
            'skill: its advertising is worked in the seller console.'
        )
    return (
        f'`{skill}` drives the seller console in a browser, and this '
        f"task's store is authorized with the ads service. Use the "
        f'`{API_SKILL}` skill and the `vibe_seller_ads_call` tool instead.'
    )


def skills_to_exclude(authorized_flags: Iterable[bool]) -> set[str]:
    """Skill directories to leave out of a task workspace.

    Args:
        authorized_flags: one flag per Amazon store the task covers —
            True when that store is bound to an ads service. Empty for a
            task with no store.

    Returns:
        Skill directory names to omit.
    """
    flags = list(authorized_flags)

    if not flags:
        # No store means no binding to use the API skill with. The
        # browser skill stays: a no-store task still has the store-less
        # web browser for neutral public work.
        return {API_SKILL}

    if all(flags):
        return {BROWSER_SKILL}
    if not any(flags):
        return {API_SKILL}

    # Mixed. Both are needed and neither can be dropped without leaving
    # some store in the task unable to work.
    return set()


# ── What a task is told ─────────────────────────────────────────────
#
# Routing the skills decides which instructions a store task CAN load;
# these say which path it is ON. Without them the agents that write the
# instructions — a schedule's planner, an all-stores orchestrator — do
# not know which stores are bound, and write one path for all: a plan
# that says "open the ad console" is handed verbatim to every store of a
# fanout, the API ones included, and "try the API first" costs every
# console store a refused call.


def label(authorized: bool) -> str:
    """How a store's ads path reads in the all-stores list."""
    return 'ads: API' if authorized else 'ads: not authorized'


#: For a task with no store: a planner or an orchestrator.
#:
#: Only the API side names a skill. A store that is not bound may sell on
#: noon, or on nothing anyone has recorded: naming the Amazon browser skill
#: for it told every such store "this is Amazon ad work", and an agent that
#: loads that skill runs its full audit procedure on a request that never
#: asked for one.
ORCHESTRATOR_NOTE = (
    'Ads API: stores marked `ads: API` are authorized with the ads service '
    '— their ad data and changes go through the `vibe_seller_ads_call` tool '
    f'(the `{API_SKILL}` skill), never a browser. The API refuses every '
    'other store; those work advertising the way they always have. Each '
    "store's own task is told whether it is authorized, so a plan or "
    'sub-task for ad work says WHAT to get (e.g. spend and ad sales for the '
    'last 30 days) and leaves HOW to the store.'
)


def store_note(authorized: bool) -> str:
    """The one line a store task gets about the ads API.

    An unbound store hears a fact, not a procedure: which skill fits its
    advertising is its own task's call, as it was before any store was
    bound.
    """
    if authorized:
        return (
            'Ads API: this store is authorized with the ads service. Do its '
            f'Amazon ad work through the `{API_SKILL}` skill and the '
            '`vibe_seller_ads_call` tool, not a browser — even where the '
            'task or its plan mentions the ad console.'
        )
    return (
        'Ads API: this store is not authorized with the ads service, so '
        '`vibe_seller_ads_call` refuses it — do not call it for this store, '
        'even where the task or its plan mentions the API.'
    )
