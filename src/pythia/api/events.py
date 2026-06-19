"""In-process SSE event broadcaster (§A.2): advisory nudges, REST snapshot is the source of truth.

Each event is a thin (channel, id) nudge; the client refetches the REST snapshot. A small replay
buffer lets a just-connected subscriber still see a very recent event, and an optional `limit` lets
a caller (and the tests) read a bounded number of events instead of an open-ended stream.
"""

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum

_REPLAY_BUFFER = 64


class SseChannel(StrEnum):
    RADAR_CHANGED = "radar_changed"
    PAPER_CHANGED = "paper_changed"
    CALIBRATION_CHANGED = "calibration_changed"


@dataclass(frozen=True, slots=True)
class SseEvent:
    channel: SseChannel
    id: str


class EventBroadcaster:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[SseEvent]] = set()
        self._recent: deque[SseEvent] = deque(maxlen=_REPLAY_BUFFER)

    def publish(self, channel: SseChannel, id: str) -> None:
        event = SseEvent(channel=channel, id=id)
        self._recent.append(event)
        for queue in self._subscribers:
            queue.put_nowait(event)

    async def subscribe(
        self, *, replay: bool = True, limit: int | None = None
    ) -> AsyncIterator[SseEvent]:
        queue: asyncio.Queue[SseEvent] = asyncio.Queue()
        self._subscribers.add(queue)
        emitted = 0
        try:
            if replay:
                for event in list(self._recent):
                    if limit is not None and emitted >= limit:
                        return
                    yield event
                    emitted += 1
            while limit is None or emitted < limit:
                yield await queue.get()
                emitted += 1
        finally:
            self._subscribers.discard(queue)
