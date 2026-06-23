"""SSE stream (§A.2): one endpoint emitting radar_changed / paper_changed / calibration_changed."""

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from pythia.api.deps import BroadcasterDep

router = APIRouter(tags=["events"])


@router.get("/api/events")
async def events(broadcaster: BroadcasterDep, limit: int | None = None) -> StreamingResponse:
    async def stream() -> AsyncIterator[str]:
        yield ": connected\n\n"  # initial flush so EventSource opens behind a buffering proxy
        async for event in broadcaster.subscribe(limit=limit):
            yield f"event: {event.channel.value}\ndata: {json.dumps({'id': event.id})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")
