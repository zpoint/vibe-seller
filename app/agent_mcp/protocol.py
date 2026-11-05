"""JSON-RPC 2.0 + MCP wire helpers for the hosted ``/api/mcp`` endpoint.

Kept deliberately small: the endpoint speaks one JSON document per
call, so the only protocol facts this module owns are the error codes,
the success/failure envelopes and the protocol-revision negotiation.
"""

from typing import Any

JSONRPC_VERSION = '2.0'

# Standard JSON-RPC 2.0 error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
# Application-level code: the request never reached a tool because the
# caller was not authenticated (HTTP 401).
UNAUTHORIZED = -32001
# Application-level code: the request was refused by the transport
# guard — cross-site where that is not allowed (HTTP 403).
FORBIDDEN = -32002

# Protocol revisions this server speaks. Mirrors the reference MCP
# implementation's ``SUPPORTED_PROTOCOL_VERSIONS`` — a client that asks
# for one of them gets the same version back, which is what the spec
# requires ("respond with the same version if the server supports it").
SUPPORTED_PROTOCOL_VERSIONS = (
    '2024-11-05',
    '2025-03-26',
    '2025-06-18',
    '2025-11-25',
)
LATEST_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[-1]


def negotiate_version(requested: Any) -> str:
    """Return the protocol revision to answer an ``initialize`` with.

    Echoes the client's requested revision when this server speaks it,
    otherwise falls back to the newest revision we speak — the same
    rule the reference server applies.
    """
    if isinstance(requested, str) and requested in SUPPORTED_PROTOCOL_VERSIONS:
        return requested
    return LATEST_PROTOCOL_VERSION


def success(request_id: Any, result: dict) -> dict:
    """A JSON-RPC success envelope."""
    return {'jsonrpc': JSONRPC_VERSION, 'id': request_id, 'result': result}


def failure(
    request_id: Any,
    code: int,
    message: str,
    data: Any = None,
) -> dict:
    """A JSON-RPC error envelope."""
    error: dict[str, Any] = {'code': code, 'message': message}
    if data is not None:
        error['data'] = data
    return {'jsonrpc': JSONRPC_VERSION, 'id': request_id, 'error': error}


def is_notification(message: dict) -> bool:
    """True when *message* is a JSON-RPC notification (no ``id``).

    ``id: 0`` is a legal request id, so the test is membership of the
    key, not truthiness of the value.
    """
    return 'id' not in message or message.get('id') is None
