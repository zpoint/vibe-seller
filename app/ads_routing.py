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

#: The browser skill, for stores with no ads service binding.
BROWSER_SKILL = 'amazon-ads'
#: The API skill, pulled from the bound service.
API_SKILL = 'amazon-ads-api'


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
