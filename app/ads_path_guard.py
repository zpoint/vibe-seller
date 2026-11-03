"""A task may not search the ads service by guessing paths.

``vibe_seller_ads_call`` is a pass-through: the skill names the paths the
service answers, and the agent may call any of them. Nothing stopped it
from calling paths the skill never named. Observed in CI: a spend report
had its answer from the one documented path within a minute, then spent
fifteen more probing over forty invented ones (``/campaigns``,
``/openapi.json``, ``/ads/summary`` ...) for fields the service does not
expose. Each 404 looked like a lead, and every call was different, so no
loop detector saw a loop.

The contract, per task: a path the service answers stays open; after
``MAX_UNKNOWN`` distinct paths the service did not know, further unknown
paths are refused before they leave the server.

**Only a missing route counts as a missing path.** The service answers
404 in two quite different situations: a path it has no route for, and
a route that has nothing to say — a store it does not know, a campaign
that is gone. Only the first is the agent guessing. Counting the second
closes a route that works and tells the agent, wrongly, that a
documented path does not exist. The two are told apart once, where the
response is read (:mod:`app.ads_client`), and arrive here as an
:class:`Outcome` — never as a bare status, which cannot tell them
apart.
"""

from collections import OrderedDict
from enum import Enum

from app.ads_client import AdsServiceError

MAX_UNKNOWN = 3
_MAX_TASKS = 512

#: task id -> (answered paths, unknown paths), oldest task evicted first.
_seen: OrderedDict[str, tuple[set[str], set[str]]] = OrderedDict()

NOT_A_PATH = (
    ' The service has no such path. Call only the paths your ads skill '
    'documents; do not guess others.'
)

_REFUSED = (
    'Blocked: {n} guessed paths were unknown to the ads service, and '
    '`{path}` is not one it has answered for this task. Work from what '
    'the documented paths returned; if the request needs a field they do '
    'not carry, say so in the result.'
)


class Outcome(Enum):
    """What a call established about the path it was made on.

    ``ANSWERED`` — the route exists: the call worked, or the route
    answered about something it does not have.
    ``UNKNOWN_ROUTE`` — the service has no such path. This is the only
    one that counts against the task.
    ``UNREACHED`` — the service was never reached, so the path was
    neither proved nor disproved.
    """

    ANSWERED = 'answered'
    UNKNOWN_ROUTE = 'unknown_route'
    UNREACHED = 'unreached'


def outcome_for(error: AdsServiceError) -> Outcome:
    """What a failed call says about the path it was made on.

    ``error.unknown_route`` is the service's own answer on whether it
    has the path at all. A status cannot say: the same 404 means both
    "no such route" and "no such store", and only the first is the
    agent guessing.
    """
    if error.unknown_route:
        return Outcome.UNKNOWN_ROUTE
    if error.status is None:
        return Outcome.UNREACHED
    return Outcome.ANSWERED


def _state(task_id: str) -> tuple[set[str], set[str]]:
    if task_id in _seen:
        _seen.move_to_end(task_id)
    else:
        _seen[task_id] = (set(), set())
        while len(_seen) > _MAX_TASKS:
            _seen.popitem(last=False)
    return _seen[task_id]


def refusal(task_id: str | None, path: str) -> str | None:
    """Why this call must not be sent, else ``None``."""
    if not task_id:
        return None
    answered, unknown = _state(task_id)
    if path in answered or len(unknown) < MAX_UNKNOWN:
        return None
    return _REFUSED.format(n=len(unknown), path=path)


def record(task_id: str | None, path: str, outcome: Outcome) -> None:
    """Note what the service said about ``path``.

    ``outcome`` is what the call established, not a status: a status
    cannot tell a missing route from a missing entity, and reading one
    as the other is how a working route gets closed. Anything that is
    not an :class:`Outcome` is refused here rather than guessed at.
    """
    outcome = Outcome(outcome)
    if not task_id:
        return
    answered, unknown = _state(task_id)
    if outcome is Outcome.ANSWERED:
        answered.add(path)
    elif outcome is Outcome.UNKNOWN_ROUTE:
        unknown.add(path)
