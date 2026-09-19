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
import time

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


# ---------------------------------------------------------------------------
# Issue #28: a full queue must not blind the producer to cancellation.
#
# When the client disconnects the consumer stops draining. If the bounded
# queue is full at that moment the producer is inside q.put(), which used to
# wait out the whole _STREAM_PRODUCER_PUT_TIMEOUT_SECONDS (30 s) without ever
# looking at cancel_event -- the model stream (and any gate slot held by the
# generator) stayed occupied for that long. Still true and still documented:
# an LLM call that is already in flight cannot be interrupted; cancellation
# takes effect at event boundaries.
# ---------------------------------------------------------------------------


class _SpyQueue(queue.Queue):
    """A queue that reports the moment put() is called while it is full, i.e.
    the producer is about to block. Lets a test cancel *while* the producer is
    stuck, without sleeping or guessing."""

    def __init__(self, maxsize: int):
        super().__init__(maxsize=maxsize)
        self.put_on_full = threading.Event()

    def put(self, item, block=True, timeout=None):
        if self.full():
            self.put_on_full.set()
        return super().put(item, block, timeout)


def test_cancel_is_noticed_within_a_short_bound_while_the_queue_is_full():
    generator_closed = threading.Event()

    def fake_gen():
        try:
            yield ("delta", {"text": "a"})  # fills the queue
            yield ("delta", {"text": "b"})  # its put() has nowhere to go
            yield ("done", {"ok": True})
        finally:
            generator_closed.set()

    q = _SpyQueue(maxsize=1)
    cancel_event = threading.Event()
    t = threading.Thread(
        target=chat_module._produce_stream_events, args=(fake_gen, q, cancel_event), daemon=True,
    )
    t.start()

    assert q.put_on_full.wait(timeout=5), "the producer never reached a put() on the full queue"
    started = time.monotonic()
    cancel_event.set()  # the consumer is gone; nobody will ever drain the queue

    assert generator_closed.wait(timeout=3), "the generator was still open long after the cancel"
    t.join(timeout=3)
    assert not t.is_alive()
    assert time.monotonic() - started < 3


def test_thread_exits_promptly_after_a_cancel_even_though_its_notice_puts_have_nowhere_to_go():
    """After the cancel check fires the producer also tries to enqueue a
    'cancelled' notice and, in the finally block, the end-of-stream sentinel.
    With a full queue and nobody listening those puts must not keep the thread
    alive for their own multi-second timeouts."""
    closed = threading.Event()
    at_checkpoint = threading.Event()
    proceed = threading.Event()

    def fake_gen():
        try:
            yield ("delta", {"text": "a"})  # fills the queue (maxsize=1)
            at_checkpoint.set()
            proceed.wait(timeout=5)
            yield ("delta", {"text": "b"})  # the cancel check fires before this is queued
        finally:
            closed.set()

    q: "queue.Queue" = queue.Queue(maxsize=1)
    cancel_event = threading.Event()
    t = threading.Thread(
        target=chat_module._produce_stream_events, args=(fake_gen, q, cancel_event), daemon=True,
    )
    t.start()

    assert at_checkpoint.wait(timeout=5)
    cancel_event.set()
    proceed.set()

    assert closed.wait(timeout=3)
    t.join(timeout=3)
    assert not t.is_alive(), "the producer thread outlived its generator waiting on a queue nobody reads"


def test_a_consumer_that_never_drains_and_never_cancels_still_makes_the_producer_give_up(monkeypatch):
    """The pre-existing fallback: if the consumer neither reads nor signals
    cancel (network trouble the ASGI layer has not reported yet) the producer
    stops waiting after _STREAM_PRODUCER_PUT_TIMEOUT_SECONDS and closes the
    generator. Shortened here so the test does not take half a minute."""
    monkeypatch.setattr(chat_module, "_STREAM_PRODUCER_PUT_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(chat_module, "_STREAM_PRODUCER_NOTICE_TIMEOUT_SECONDS", 0.3, raising=False)
    closed = threading.Event()

    def fake_gen():
        try:
            yield ("delta", {"text": "a"})
            yield ("delta", {"text": "b"})
            yield ("done", {"ok": True})
        finally:
            closed.set()

    q: "queue.Queue" = queue.Queue(maxsize=1)
    cancel_event = threading.Event()  # never set
    t = threading.Thread(
        target=chat_module._produce_stream_events, args=(fake_gen, q, cancel_event), daemon=True,
    )
    t.start()

    assert closed.wait(timeout=3), "the producer never gave up on a queue that was never drained"
