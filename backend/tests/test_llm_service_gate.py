"""
PR2B-4 回归测试：LLMClient.chat/stream_chat 接入模型调用闸门

`chat()` 现有的 @retry 没有白名单也没有 reraise=True——闸门必须包在
_chat_with_retry 外面一层，一次逻辑调用只进一次闸门，不会因为内部重试
2 次而被重复计数、也不会让 ModelCallAdmissionTimeout 被盲目重试。
`stream_chat()` 是生成器，闸门要横跨整个生成周期，正常耗尽和被外部提前
`.close()` 都要正确释放。
"""
from __future__ import annotations

import time
from unittest.mock import Mock

import pytest

from app.core.model_gate import ModelCallAdmissionTimeout
from app.services import llm_service as llm_module
from app.services.llm_service import LLMClient


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    # tenacity 的 wait_fixed(1) 内部通过 tenacity.nap.sleep -> time.sleep(...)
    # 走的是共享 time 模块的实时属性查找（不是装饰时绑定的引用），直接打桩
    # 全局 time.sleep 就能生效，不用去猜 tenacity 内部具体绑定方式。
    monkeypatch.setattr(time, "sleep", lambda *_: None)


def test_chat_acquires_gate_once_per_call_not_per_retry(monkeypatch):
    client = LLMClient()
    monkeypatch.setattr(client, "_resolve_provider", lambda: ("openai", "http://x", "key", "model"))

    calls = {"n": 0}

    def fake_openai_chat(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return "answer"

    monkeypatch.setattr(llm_module, "_openai_chat", fake_openai_chat)

    gate_enter_count = {"n": 0}

    class _CountingGate:
        def __enter__(self):
            gate_enter_count["n"] += 1
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(llm_module, "acquire_model_call_slot", lambda kind: _CountingGate())

    result = client.chat("sys", "user")
    assert result == "answer"
    assert calls["n"] == 2  # retried once internally
    assert gate_enter_count["n"] == 1  # but the gate was only entered once


def test_admission_timeout_is_not_retried_and_propagates_immediately(monkeypatch):
    client = LLMClient()
    retry_body = Mock()
    monkeypatch.setattr(client, "_chat_with_retry", retry_body)

    def raising_gate(kind):
        raise ModelCallAdmissionTimeout(kind, 30.0)

    monkeypatch.setattr(llm_module, "acquire_model_call_slot", raising_gate)

    with pytest.raises(ModelCallAdmissionTimeout):
        client.chat("sys", "user")
    retry_body.assert_not_called()


def test_stream_chat_holds_gate_for_whole_generator_lifetime(monkeypatch):
    client = LLMClient()
    monkeypatch.setattr(client, "_resolve_provider", lambda: ("openai", "http://x", "key", "model"))

    gate_state = {"held": False, "released": False}

    class _TrackingGate:
        def __enter__(self):
            gate_state["held"] = True
            return self

        def __exit__(self, *exc):
            gate_state["held"] = False
            gate_state["released"] = True
            return False

    monkeypatch.setattr(llm_module, "acquire_model_call_slot", lambda kind: _TrackingGate())

    def fake_stream(*args, **kwargs):
        assert gate_state["held"] is True
        yield "a"
        assert gate_state["held"] is True
        yield "b"

    monkeypatch.setattr(llm_module, "_openai_stream_chat", fake_stream)

    gen = client.stream_chat("sys", "user")
    assert next(gen) == "a"
    assert gate_state["held"] is True
    assert gate_state["released"] is False

    # Drain to completion -- gate must be released after normal exhaustion.
    remaining = list(gen)
    assert remaining == ["b"]
    assert gate_state["released"] is True


def test_stream_chat_releases_gate_when_closed_early(monkeypatch):
    client = LLMClient()
    monkeypatch.setattr(client, "_resolve_provider", lambda: ("openai", "http://x", "key", "model"))

    gate_state = {"released": False}

    class _TrackingGate:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            gate_state["released"] = True
            return False

    monkeypatch.setattr(llm_module, "acquire_model_call_slot", lambda kind: _TrackingGate())

    def fake_stream(*args, **kwargs):
        yield "a"
        yield "b"
        yield "c"

    monkeypatch.setattr(llm_module, "_openai_stream_chat", fake_stream)

    gen = client.stream_chat("sys", "user")
    assert next(gen) == "a"
    assert gate_state["released"] is False

    gen.close()
    assert gate_state["released"] is True
