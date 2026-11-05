"""The tool registry: what a tool IS, and how the set is served.

Split out of ``tools.py`` when that module passed the repo's 800-line
limit. The registry is the only thing the endpoint's dispatcher needs
(``find_tool`` / ``tool_definitions``); the tool implementations live
in ``tools_tasks.py`` and ``tools_workspace.py`` and register here at
import time via the :func:`tool` decorator.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.agent_mcp.support import McpContext


@dataclass
class ToolDef:
    """One MCP tool: its wire schema and the handler behind it."""

    name: str
    description: str
    input_schema: dict
    handler: Callable[['McpContext', dict], Awaitable[Any]]


TOOLS: list[ToolDef] = []
_TOOLS_BY_NAME: dict[str, ToolDef] = {}


def tool(name: str, description: str, input_schema: dict):
    """Register a tool: name, description and JSON Schema for its args.

    The decorator runs at import time, so importing a tool module is
    what makes its tools appear in ``tools/list`` — ``tools.py`` is the
    assembly point that imports them all.
    """

    def decorator(func):
        definition = ToolDef(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=func,
        )
        TOOLS.append(definition)
        _TOOLS_BY_NAME[name] = definition
        return func

    return decorator


def schema(
    properties: dict | None = None,
    required: list[str] | None = None,
) -> dict:
    """An object JSON Schema for a tool's arguments.

    ``additionalProperties: false`` is a promise the dispatcher keeps:
    an unknown argument is rejected with the accepted names listed, not
    silently ignored (see ``router._argument_problem``).
    """
    return {
        'type': 'object',
        'properties': properties or {},
        'required': required or [],
        'additionalProperties': False,
    }


def tool_definitions() -> list[dict]:
    """The ``tools/list`` payload, sorted by name.

    Registration order is import order, which moves whenever a tool
    moves between modules — not something a client should ever see.
    The serving order is alphabetical so it is stable regardless of
    how the implementations are laid out.
    """
    return [
        {
            'name': definition.name,
            'description': definition.description,
            'inputSchema': definition.input_schema,
        }
        for definition in sorted(TOOLS, key=lambda d: d.name)
    ]


def find_tool(name: str) -> ToolDef | None:
    """The tool called *name*, if there is one."""
    return _TOOLS_BY_NAME.get(name)
