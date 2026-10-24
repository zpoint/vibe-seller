"""The agent's todo list: one shape, checked where it enters.

A ``TodoWrite`` tool call carries the agent's checklist. The server
mirrors it onto ``Task.todos`` (the GUI's checklist, and the finalize
step's "did the agent stop mid-list?" signal). The tool input arrives
as the model wrote it, before the CLI validates it. In CI a model sent
``{"todos": {"item": {...}}}``: the CLI rejected the call, but the
server stored the dict, and finalize iterated its keys and crashed the
task with ``'str' object has no attribute 'get'``.

A list is a todo list only if every item is an object with a string
``content`` and a known ``status``. Anything else is not the agent's
checklist: the CLI refused it, and the server ignores it.
"""

import json
from typing import Any

STATUSES = frozenset({'pending', 'in_progress', 'completed'})


def valid_todos(value: Any) -> list[dict] | None:
    """``value`` if it is a well-formed todo list, else ``None``."""
    if not isinstance(value, list):
        return None
    for item in value:
        if not isinstance(item, dict):
            return None
        if not isinstance(item.get('content'), str):
            return None
        if item.get('status') not in STATUSES:
            return None
    return value


def parse_stored(raw: str | None) -> list[dict] | None:
    """The stored list, or ``None`` if it is missing or malformed."""
    if not raw:
        return None
    try:
        return valid_todos(json.loads(raw))
    except (json.JSONDecodeError, TypeError):
        return None
