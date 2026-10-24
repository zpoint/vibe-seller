"""Refuse a vibe-seller MCP call that is missing required arguments.

Observed in CI: a model that had just written a catalog emitted ten
parallel ``vibe_seller_write_workspace_file`` calls with ``{}`` as the
input. Each reached the MCP server and came back as ``{"error":
"'path'"}`` -- a bare ``KeyError`` that names nothing -- and the ten
identical signatures tripped the loop circuit breaker, which stopped a
session whose deliverable was already on disk. A batch the model sent
before seeing a single result is not a loop; it is a malformed request.

The server owns these tools' schemas (``app.mcp_tool_schemas.TOOLS``),
so the contract is checked where the call enters: before it runs and
before the loop detector counts it, with a reason that says which
arguments are missing.
"""

from app.mcp_tool_schemas import TOOLS

# Claude Code's name for a tool served by the ``vibe-seller`` MCP server.
_PREFIX = 'mcp__vibe-seller__'

_REQUIRED = {
    t['name']: tuple(t.get('inputSchema', {}).get('required') or ())
    for t in TOOLS
}


def missing_args_deny(tool_name: str, tool_input) -> str | None:
    """Deny reason when a vibe-seller MCP call omits a required argument."""
    if not tool_name.startswith(_PREFIX):
        return None
    required = _REQUIRED.get(tool_name[len(_PREFIX) :], ())
    args = tool_input if isinstance(tool_input, dict) else {}
    missing = [k for k in required if k not in args]  # schema `required`
    if not missing:
        return None
    return (
        f'{tool_name} was called without its required argument(s): '
        f'{", ".join(missing)}. Nothing ran. If you still need this call, '
        'make it once with every required argument filled in; if the work '
        'is already done, do not call it again.'
    )
