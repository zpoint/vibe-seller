import asyncio

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from app.events.bus import event_bus

router = APIRouter(prefix='/api/sse', tags=['sse'])


@router.get('')
async def sse_events():
    queue = event_bus.subscribe()

    async def stream():
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=30)
                    yield {'data': msg}
                except TimeoutError:
                    yield {'data': '{"type":"ping"}'}
        except asyncio.CancelledError:
            pass
        finally:
            event_bus.unsubscribe(queue)

    return EventSourceResponse(stream())
