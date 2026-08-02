"""Shared dataclasses for the CDP multiplexing proxy."""

from __future__ import annotations

from dataclasses import dataclass, field

from websockets.asyncio.server import ServerConnection

# Close code sent to a connection whose client id a NEWER connection
# claimed. Distinct from 4029 (capacity) so the daemon-side error, and
# the log line, name the actual problem. Lives here rather than in
# cdp_mux_proxy because cdp_mux_routing needs it too and importing the
# proxy from the mixin it is built from would be circular.
DUPLICATE_CLIENT_CLOSE_CODE = 4409


def short_cid(client_id: str, width: int = 8) -> str:
    """Log-friendly client id that still distinguishes worker slots.

    Ownership log lines used to truncate with ``client_id[:8]``, which
    was unambiguous only while every client WAS a distinct task uuid.
    Parallel worker slots append the suffix (``<task-uuid>-w2``), so a
    head-truncation renders a task's main client and all of its workers
    as the same string — and target-ownership, createTarget,
    attachToTarget and tab-cap lines all become unattributable exactly
    when you most need them (debugging concurrent drivers).

    Keep the head for correlation and re-attach the slot::

        1a2b3c4d-0000-0000-…        -> 1a2b3c4d
        1a2b3c4d-0000-0000-…-w2     -> 1a2b3c4d-w2
    """
    head, sep, tail = client_id.rpartition('-')
    if sep and len(tail) >= 2 and tail[0] == 'w' and tail[1:].isdigit():
        return f'{head[:width]}-{tail}'
    return client_id[:width]


@dataclass
class ClientState:
    """Tracks a single downstream client (browser-use CLI)."""

    client_id: str
    ws: ServerConnection
    target_ids: set[str] = field(default_factory=set)
    session_ids: set[str] = field(default_factory=set)
    # Creation order of owned targets — the LRU basis for the per-client
    # tab cap (every navigation is a new_tab, so a long task accumulates
    # tabs unboundedly without it). Kept in sync with target_ids.
    target_order: list[str] = field(default_factory=list)


@dataclass
class RequestMapping:
    """Maps a global request ID back to the originating client."""

    client_id: str
    original_id: int
    session_id: str | None = None
    is_create_target: bool = False
    is_attach_target: bool = False
