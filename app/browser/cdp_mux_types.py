"""Shared dataclasses for the CDP multiplexing proxy."""

from __future__ import annotations

from dataclasses import dataclass, field

from websockets.asyncio.server import ServerConnection


@dataclass
class ClientState:
    """Tracks a single downstream client (browser-use CLI)."""

    client_id: str
    ws: ServerConnection
    target_ids: set[str] = field(default_factory=set)
    session_ids: set[str] = field(default_factory=set)


@dataclass
class RequestMapping:
    """Maps a global request ID back to the originating client."""

    client_id: str
    original_id: int
    session_id: str | None = None
    is_create_target: bool = False
    is_attach_target: bool = False
