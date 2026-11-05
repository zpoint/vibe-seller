"""Helpers for the action-style tools (``manage_x(action=...)``).

The operator-parity tools pack a resource's CRUD surface into ONE
tool with an ``action`` enum (design rule: a tool list an external
agent can hold in context). That packing moves the per-action
argument contract out of the JSON Schema — the schema is the UNION
of every action's arguments — into the handler, so every action
validates its own required fields up front and names the missing
one. These helpers keep that validation one line per field instead
of a typed ``if`` forest, and they raise :class:`ToolError`, so the
answer is a tool error the agent can act on, never a 500.
"""

from app.agent_mcp.registry import schema
from app.agent_mcp.support import ToolError


def action_arg(args: dict, allowed: tuple[str, ...]) -> str:
    """Read and validate the ``action`` argument of an action tool.

    An action outside *allowed* names every accepted action — an
    agent that guesses ``remove`` needs to be told the word is
    ``delete``, not shown a protocol error.
    """
    value = args.get('action')
    if value not in allowed:
        raise ToolError(
            f'action must be one of: {", ".join(allowed)} (got {value!r})'
        )
    return value


def opt_str(args: dict, key: str) -> str | None:
    """An optional string argument; empty string counts as absent.

    Empty-means-absent matches how the tool schemas declare the
    fields (``['string', 'null']``) and lets a caller omit a field
    without caring whether the client sends ``null`` or ``''``.
    """
    value = args.get(key)
    if value is None or value == '':
        return None
    if not isinstance(value, str):
        raise ToolError(f'{key} must be a string')
    return value


def opt_bool(args: dict, key: str) -> bool | None:
    """An optional boolean argument."""
    value = args.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ToolError(f'{key} must be true or false')
    return value


def opt_int(args: dict, key: str) -> int | None:
    """An optional integer argument."""
    value = args.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ToolError(f'{key} must be an integer')
    return value


def flag(args: dict, key: str, default: bool = False) -> bool:
    """A boolean argument that falls back to the ROUTE's default.

    For optional flags the meaningful default is the one the REST
    schema gives the web UI (``use_ssl=True``, ``force=False`` …) —
    "absent" must behave exactly as it does for a person, so the
    tool passes the schema default through instead of inventing its
    own.
    """
    value = args.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ToolError(f'{key} must be true or false')
    return value


def action_schema(
    properties: dict,
    *,
    actions: tuple[str, ...],
    action_description: str = 'Which operation to run.',
) -> dict:
    """The input schema of an action tool: ``action`` plus its union
    of arguments, in the registry's strict object form."""
    merged = {
        'action': {
            'type': 'string',
            'enum': list(actions),
            'description': action_description,
        },
        **(properties or {}),
    }
    return schema(merged, required=['action'])
