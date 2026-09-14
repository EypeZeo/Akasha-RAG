"""
PR2B-5 回归测试：/ask/stream 的生产者线程 + 有界队列桥接逻辑

`answer_stream()` 是同步生成器，不能直接 `async for` 驱动而不阻塞事件
循环。`_produce_stream_events` 在独立线程里完整驱动它，通过有界队列把
事件转交给异步消费者；取消信号走反方向（`cancel_event`）。这里只测试
桥接/取消逻辑本身，用一个自制的假生成器精确控制时序（`threading.Event`
同步，不用 sleep），不涉及真实 ASGI/TestClient/网络。
"""
from __future__ import annotations

import queue
import threading

import pytest

from app.api.routes import chat as chat_module


def _drain(q: "queue.Queue", timeout: float = 5.0) -> list:
    items = []
    while True:
        item = q.get(timeout=timeout)
        if item is chat_module._STREAM_SENTINEL:
            return items
        items.append(item)


def test_produces_items_in_order_until_normal_exhaustion():
    closed = {"v": False}

    def fake_gen():
        try:
            yield ("sources", {"sources": []})
            yield ("delta", {"text": "a"})
            yield ("delta", {"text": "b"})
            yield ("done", {"ok": True})
        finally:
            closed["v"] = True

    q: "queue.Queue" = queue.Queue(maxsize=8)
    cancel_event = threading.Event()
    t = threading.Thread(target=chat_module._produce_stream_events, args=(fake_gen, q, cancel_event))
    t.start()
    t.join(timeout=5)
    assert not t.is_alive()

    items = _drain(q)
    assert items == [
        ("sources", {"sources": []}),
        ("delta", {"text": "a"}),
        ("delta", {"text": "b"}),
        ("done", {"ok": True}),
    ]
    assert closed["v"] is True


def test_cancellation_stops_before_further_items_and_closes_generator():
    closed = {"v": False}
    reached_checkpoint = threading.Event()
    proceed = threading.Event()

    def fake_gen():
        try:
            yield ("delta", {"text": "a"})
            reached_checkpoint.set()
            proceed.wait(timeout=5)
            yield ("delta", {"text": "b"})  # must never reach the queue
            yield ("done", {"ok": True})
        finally:
            closed["v"] = True

    q: "queue.Queue" = queue.Queue(maxsize=8)
    cancel_event = threading.Event()
    t = threading.Thread(target=chat_module._produce_stream_events, args=(fake_gen, q, cancel_event))
    t.start()

    assert reached_checkpoint.wait(timeout=5)
    cancel_event.set()
    proceed.set()
    t.join(timeout=5)
    assert not t.is_alive()

    items = _drain(q)
    assert items == [
        ("delta", {"text": "a"}),
        ("cancelled", {"message": "客户端已断开"}),
    ]
    assert closed["v"] is True


def test_producer_relays_generator_exception_as_error_event():
    def fake_gen():
        yield ("delta", {"text": "a"})
        raise RuntimeError("boom")

    q: "queue.Queue" = queue.Queue(maxsize=8)
    cancel_event = threading.Event()
    t = threading.Thread(target=chat_module._produce_stream_events, args=(fake_gen, q, cancel_event))
    t.start()
    t.join(timeout=5)

    items = _drain(q)
    assert items == [
        ("delta", {"text": "a"}),
        ("error", {"message": "boom"}),
    ]


def test_producer_blocks_on_a_full_queue_without_dropping_items():
    """A bounded queue (maxsize=1) forces the producer to repeatedly block
    on q.put() while nothing drains it yet -- the deterministic property
    under test is that no item gets dropped or duplicated once the
    consumer eventually catches up, not any timing about when blocking
    happens."""
    def fake_gen():
        for i in range(5):
            yield ("delta", {"text": str(i)})
        yield ("done", {"ok": True})

    q: "queue.Queue" = queue.Queue(maxsize=1)
    cancel_event = threading.Event()

    t = threading.Thread(target=chat_module._produce_stream_events, args=(fake_gen, q, cancel_event))
    t.start()
    items = _drain(q)
    t.join(timeout=5)

    assert not t.is_alive()
    assert items == [("delta", {"text": str(i)}) for i in range(5)] + [("done", {"ok": True})]
