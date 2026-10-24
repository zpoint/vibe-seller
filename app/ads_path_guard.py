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
"""

from collections import OrderedDict

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


def record(task_id: str | None, path: str, status: int | None) -> None:
    """Note how the service answered ``path`` (``None`` = success)."""
    if not task_id:
        return
    answered, unknown = _state(task_id)
    if status is None:
        answered.add(path)
    elif status == 404:
        unknown.add(path)
