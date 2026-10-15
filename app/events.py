"""Pub/sub de eventos por canal (documento ou "exports"), entregues ao event loop do servidor."""
import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager

Publisher = Callable[[str, dict], None]


class EventBus:
    def __init__(self):
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def publish(self, channel: str, event: dict):
        """Pode ser chamado de qualquer thread."""
        if self._loop is None:
            return
        for q in list(self._subscribers.get(channel, ())):
            self._loop.call_soon_threadsafe(q.put_nowait, event)

    @contextmanager
    def subscribe(self, channel: str) -> Iterator[asyncio.Queue]:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(channel, set()).add(q)
        try:
            yield q
        finally:
            self._subscribers[channel].discard(q)
