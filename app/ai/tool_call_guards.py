"""Per-call refusals the PreToolUse hook applies to every tool.

One entry point so the hook (at its line budget) chains a single call:
a vibe-seller MCP call missing required arguments
(``mcp_args_guard``), or any tool reaching into vibe-seller's own source
(``source_guard``).
"""

from app.ai.mcp_args_guard import missing_args_deny
from app.ai.source_guard import source_access_deny


def tool_call_deny(tool_name: str, tool_input) -> str | None:
    """First refusal for this tool call, else ``None``."""
    return missing_args_deny(tool_name, tool_input) or source_access_deny(
        tool_name, tool_input
    )
