"""
PR2B-4 回归测试：跨服务并发上限（这个批次真正要修的 bug）

入库流水线（≤3 个工作线程）与任意数量并发的 /ask 请求分别调用
LLMClient.chat / EmbeddingClient._embed_batch / VisionService._call_vision_model
时，此前完全没有进程级并发上限。这里把 `model_call_max_concurrency` 设成 2，
让"3 个模拟入库线程 + 1 个模拟 /ask 线程"各自真实调用对应的公开方法。

不用 sleep 判定"确实发生了并发"：用一个 parties=2 的 Barrier 放在闸门
内部——只有真的有 2 个线程同时持有闸门时，这个 Barrier 才会立刻解开；
如果闸门退化成只允许 1 个并发，第一个线程会在 Barrier 上等到超时，
测试会用一个明确的 BrokenBarrierError 失败，而不是悄悄地只观察到 1。
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from app.core import model_gate
from app.services import llm_service as llm_module
from app.services import vision_service as vision_module
from app.services.llm_service import EmbeddingClient, LLMClient
from app.services.vision_service import VisionService


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    import time as time_module
    monkeypatch.setattr(time_module, "sleep", lambda *_: None)
    monkeypatch.setattr(vision_module.time, "sleep", lambda *_: None)


def test_concurrency_never_exceeds_the_configured_cap_across_llm_embedding_and_vision(monkeypatch):
    max_concurrency = 2
    monkeypatch.setattr(model_gate, "_gate", threading.BoundedSemaphore(max_concurrency))
    monkeypatch.setattr(model_gate.settings, "model_call_admission_timeout_seconds", 10.0)

    inside_count = 0
    max_observed = 0
    lock = threading.Lock()
    # Rendezvous point *inside* the gate: only resolves once `max_concurrency`
    # threads are simultaneously past their acquire_model_call_slot() -- this
    # is what proves real overlap happened, without any wall-clock sleep.
    rendezvous = threading.Barrier(max_concurrency, timeout=10)

    def _enter_and_hold():
        nonlocal inside_count, max_observed
        with lock:
            inside_count += 1
            max_observed = max(max_observed, inside_count)
        rendezvous.wait()
        with lock:
            inside_count -= 1

    llm_client = LLMClient()
    monkeypatch.setattr(llm_client, "_resolve_provider", lambda: ("openai", "http://x", "key", "model"))

    def fake_openai_chat(*args, **kwargs):
        _enter_and_hold()
        return "ok"

    monkeypatch.setattr(llm_module, "_openai_chat", fake_openai_chat)

    embedding_client = EmbeddingClient()
    monkeypatch.setattr(llm_module.settings, "dashscope_api_key", "fixture-secret")

    def fake_embedding_call(**kwargs):
        _enter_and_hold()
        return SimpleNamespace(status_code=200, output={
            "embeddings": [{"text_index": i, "embedding": [1.0, 2.0]} for i in range(len(kwargs["input"]))]
        })

    monkeypatch.setattr(llm_module.TextEmbedding, "call", fake_embedding_call)

    def fake_vision_call(**kwargs):
        _enter_and_hold()
        return SimpleNamespace(status_code=200, output=None, message="")

    monkeypatch.setattr(vision_module, "MultiModalConversation", SimpleNamespace(call=fake_vision_call))

    start_barrier = threading.Barrier(4, timeout=10)

    def ingestion_worker_llm():
        start_barrier.wait()
        llm_client.chat("sys", "user")

    def ingestion_worker_embedding():
        start_barrier.wait()
        embedding_client._embed_batch(["a"])

    def ingestion_worker_vision():
        start_barrier.wait()
        VisionService._call_vision_model([])

    def ask_request_llm():
        start_barrier.wait()
        llm_client.chat("sys2", "user2")

    threads = [
        threading.Thread(target=ingestion_worker_llm),
        threading.Thread(target=ingestion_worker_embedding),
        threading.Thread(target=ingestion_worker_vision),
        threading.Thread(target=ask_request_llm),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert all(not t.is_alive() for t in threads)

    assert max_observed == max_concurrency, (
        f"observed {max_observed} concurrent model calls, expected exactly the configured cap of {max_concurrency}"
    )
