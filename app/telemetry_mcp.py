"""Telemetry for the hosted MCP endpoint (``/api/mcp``).

Anonymous + privacy-safe, see ``app.telemetry``. A tool call reports
only its NAME (a fixed enum), the outcome, a bounded error category
and a duration — never arguments, task text, ids or the token.
"""

import time

from app import telemetry
from app.telemetry_events import McpClientName, TelemetryEvent

# Wall-clock timestamps of tool calls in the last 24h — the same
# shape as ``app.ads_client.api_calls_last_24h`` (process-local; a
# restart starts the window over).
_CALL_TIMES: list[float] = []


def record_mcp_call() -> None:
    _CALL_TIMES.append(time.time())


def mcp_calls_last_24h() -> int:
    cutoff = time.time() - 86400
    _CALL_TIMES[:] = [t for t in _CALL_TIMES if t >= cutoff]
    return len(_CALL_TIMES)


def send_mcp_session_started(
    *, client_name: str | None, protocol_version: str
) -> None:
    telemetry.send(
        TelemetryEvent.MCP_SESSION_STARTED,
        {
            'client_name': McpClientName.bucket(client_name),
            'protocol_version': protocol_version,
        },
    )


def send_mcp_tool_called(
    *,
    tool: str,
    outcome: str,
    error_category: str | None,
    duration_ms: int,
    user_id: str | None,
) -> None:
    record_mcp_call()
    props = {
        'tool': tool,
        'outcome': outcome,
        'duration_ms': max(0, duration_ms),
        'user_hash': telemetry.user_hash(user_id),
        'via': 'mcp',
    }
    if error_category:
        props['error_category'] = error_category
    telemetry.send(TelemetryEvent.MCP_TOOL_CALLED, props)


__all__ = [
    'mcp_calls_last_24h',
    'send_mcp_session_started',
    'send_mcp_tool_called',
]
