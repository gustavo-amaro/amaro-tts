import asyncio
import json
import threading

from app.api import sse
from app.events import EventBus


def test_publish_without_loop_is_noop():
    bus = EventBus()
    with bus.subscribe("c") as q:
        bus.publish("c", {"type": "x"})
        assert q.empty()


def test_publish_from_other_thread_reaches_only_channel_subscribers():
    async def main():
        bus = EventBus()
        bus.bind(asyncio.get_running_loop())
        with bus.subscribe("a") as qa, bus.subscribe("b") as qb:
            t = threading.Thread(target=bus.publish, args=("a", {"type": "x"}))
            t.start()
            t.join()
            assert await asyncio.wait_for(qa.get(), 1) == {"type": "x"}
            await asyncio.sleep(0)
            assert qb.empty()
        bus.publish("a", {"type": "ninguém escuta"})  # sem assinantes: não falha
        return bus

    bus = asyncio.run(main())
    assert bus._subscribers == {"a": set(), "b": set()}


def test_sse_stream_sends_heartbeat_events_and_unsubscribes(monkeypatch):
    monkeypatch.setattr(sse, "HEARTBEAT_SECONDS", 0.01)

    async def main():
        bus = EventBus()
        bus.bind(asyncio.get_running_loop())
        it = sse.sse_response(bus, "d1", lambda: {"type": "engine", "status": "ready"}).body_iterator
        frames = [await it.__anext__()]
        bus.publish("d1", {"type": "chunk_ready", "id": 3})
        frames.append(await it.__anext__())
        frames.append(await it.__anext__())  # sem eventos: heartbeat por timeout
        assert len(bus._subscribers["d1"]) == 1
        await it.aclose()
        assert bus._subscribers["d1"] == set()
        return frames

    frames = asyncio.run(main())
    assert all(f.startswith("data: ") and f.endswith("\n\n") for f in frames)
    assert [json.loads(f[6:]) for f in frames] == [
        {"type": "engine", "status": "ready"},
        {"type": "chunk_ready", "id": 3},
        {"type": "engine", "status": "ready"},
    ]
