import asyncio
import json
from collections.abc import Callable

from fastapi.responses import StreamingResponse

from ..events import EventBus

HEARTBEAT_SECONDS = 15


def sse_response(bus: EventBus, channel: str, heartbeat: Callable[[], dict]) -> StreamingResponse:
    """Streams the channel's events; when idle, sends `heartbeat()` periodically."""

    async def stream():
        with bus.subscribe(channel) as q:
            yield f"data: {json.dumps(heartbeat())}\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    ev = heartbeat()
                yield f"data: {json.dumps(ev)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
