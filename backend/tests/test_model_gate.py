"""
PR2B-4 回归测试：进程级模型调用准入闸门

用真实线程 + Barrier 证明恰好 `model_call_max_concurrency` 个调用能同时
持有闸门、第 N+1 个仍被阻塞；准入超时的断言不做真实等待（打桩信号量本身
立即返回 False），避免任何 wall-clock 依赖。
"""
from __future__ import annotations

import threading

import pytest

from app.core import model_gate


def test_gate_admits_exactly_configured_concurrency_then_blocks(monkeypatch):
    max_concurrency = 3
    monkeypatch.setattr(model_gate, "_gate", threading.BoundedSemaphore(max_concurrency))
    monkeypatch.setattr(model_gate.settings, "model_call_admission_timeout_seconds", 5.0)

    inside_count = 0
    inside_lock = threading.Lock()
    all_inside = threading.Barrier(max_concurrency + 1)
    release = threading.Event()

    def holder():
        nonlocal inside_count
        with model_gate.acquire_model_call_slot("test"):
            with inside_lock:
                inside_count += 1
            all_inside.wait(timeout=5)
            release.wait(timeout=5)

    threads = [threading.Thread(target=holder) for _ in range(max_concurrency)]
    for t in threads:
        t.start()
    all_inside.wait(timeout=5)
    assert inside_count == max_concurrency

    # A 4th caller must not be able to acquire immediately while all slots
    # are held -- prove it via a non-blocking probe on the same semaphore.
    extra_acquired = model_gate._gate.acquire(timeout=0.1)
    assert extra_acquired is False

    release.set()
    for t in threads:
        t.join(timeout=5)
    # Now that the holders released, the extra probe (never acquired, so
    # nothing to release for it) plus one fresh acquire should succeed.
    assert model_gate._gate.acquire(timeout=1.0) is True
    model_gate._gate.release()


def test_gate_raises_distinguishable_timeout_when_saturated(monkeypatch):
    class _AlwaysFullSemaphore:
        def acquire(self, timeout=None):
            return False

        def release(self):
            raise AssertionError("release() must not be called when acquire() failed")

    monkeypatch.setattr(model_gate, "_gate", _AlwaysFullSemaphore())
    monkeypatch.setattr(model_gate.settings, "model_call_admission_timeout_seconds", 12.5)

    with pytest.raises(model_gate.ModelCallAdmissionTimeout) as exc_info:
        with model_gate.acquire_model_call_slot("llm_chat"):
            pytest.fail("should never enter the with-block body")

    assert exc_info.value.call_kind == "llm_chat"
    assert exc_info.value.waited_seconds == 12.5


def test_gate_releases_on_exception_inside_the_block(monkeypatch):
    monkeypatch.setattr(model_gate, "_gate", threading.BoundedSemaphore(1))

    with pytest.raises(ValueError):
        with model_gate.acquire_model_call_slot("test"):
            raise ValueError("boom")

    # Slot must have been released despite the exception.
    assert model_gate._gate.acquire(timeout=0.1) is True
    model_gate._gate.release()
