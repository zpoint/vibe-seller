import asyncio
import json
from typing import Any


class EventBus:
    """Simple asyncio broadcast event bus for SSE streaming."""

    def __init__(self):
        self._subscribers: list[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self._subscribers = [s for s in self._subscribers if s is not q]

    async def emit(self, event_type: str, data: dict[str, Any]):
        msg = json.dumps({'type': event_type, **data})
        for q in self._subscribers:
            await q.put(msg)


# Singleton
event_bus = EventBus()
