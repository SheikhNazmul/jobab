"""Live updates for the dashboard and the chat widget (server-sent events).

Publishers call `bus.publish(topic, name, data)`. Each open browser tab holds
one subscription. Topics are `shop:<id>` for the dashboard and
`conversation:<id>` for a website-chat visitor.
"""

import asyncio
import json
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

HEARTBEAT_SECONDS = 20


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, topic: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers[topic].add(queue)
        return queue

    def unsubscribe(self, topic: str, queue: asyncio.Queue) -> None:
        self._subscribers[topic].discard(queue)
        if not self._subscribers[topic]:
            del self._subscribers[topic]

    def publish(self, topic: str, name: str, data: dict[str, Any] | None = None) -> None:
        for queue in list(self._subscribers.get(topic, ())):
            if queue.full():
                # A stuck tab loses its oldest event; it resyncs on reconnect.
                queue.get_nowait()
            queue.put_nowait((name, data or {}))


bus = EventBus()


async def sse_stream(topic: str) -> AsyncIterator[bytes]:
    queue = bus.subscribe(topic)
    try:
        yield b"retry: 3000\n\n"
        while True:
            try:
                name, data = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
            except TimeoutError:
                yield b": ping\n\n"
                continue
            payload = json.dumps(data, default=str, separators=(",", ":"))
            yield f"event: {name}\ndata: {payload}\n\n".encode()
    finally:
        bus.unsubscribe(topic, queue)
