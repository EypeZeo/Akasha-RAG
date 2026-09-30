"""Cloud client/stream cleanup without making network calls."""
from types import SimpleNamespace

import pytest
import httpx
from unittest.mock import Mock

from app.services import llm_service as module


class ManagedResource:
    def __init__(self):
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True


def install_client(monkeypatch, create):
    client = ManagedResource()
    client.chat = SimpleNamespace(completions=SimpleNamespace(create=create))
    def create_client(**kwargs):
        assert kwargs["max_retries"] == 0
        return client
    monkeypatch.setattr(module, "OpenAI", create_client)
    return client


@pytest.mark.parametrize("failure", [False, True])
def test_chat_closes_client_on_success_and_failure(monkeypatch, failure):
    def create(**_):
        if failure:
            raise RuntimeError("transport failed")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="answer"))])

    client = install_client(monkeypatch, create)
    if failure:
        with pytest.raises(RuntimeError, match="transport failed"):
            module._openai_chat("https://example.test", "key", "model", "sys", "user", .6, 10, 1)
    else:
        assert module._openai_chat("https://example.test", "key", "model", "sys", "user", .6, 10, 1) == "answer"
    assert client.closed


@pytest.mark.parametrize("status,attempts", [(400, 1), (401, 1), (403, 1), (404, 1), (429, 3), (503, 3)])
def test_chat_retries_only_transient_statuses(monkeypatch, status, attempts):
    monkeypatch.setattr(module.time, "sleep", lambda *_: None)
    request = httpx.Request("POST", "https://example.test/chat")
    response = httpx.Response(status, request=request)
    error = httpx.HTTPStatusError("upstream error", request=request, response=response)
    call = Mock(side_effect=error)
    monkeypatch.setattr(module, "_openai_chat", call)
    client = module.LLMClient()
    monkeypatch.setattr(client, "_resolve_provider", lambda: ("openai", "https://example.test", "key", "model"))
    with pytest.raises(httpx.HTTPStatusError):
        client.chat("system", "user")
    assert call.call_count == attempts


def test_chat_transport_failure_has_three_attempt_limit(monkeypatch):
    monkeypatch.setattr(module.time, "sleep", lambda *_: None)
    call = Mock(side_effect=httpx.ConnectError("disconnected"))
    monkeypatch.setattr(module, "_openai_chat", call)
    client = module.LLMClient()
    monkeypatch.setattr(client, "_resolve_provider", lambda: ("openai", "https://example.test", "key", "model"))
    with pytest.raises(httpx.ConnectError):
        client.chat("system", "user")
    assert call.call_count == 3


def test_chat_local_cancellation_error_is_not_retried(monkeypatch):
    call = Mock(side_effect=RuntimeError("cancelled"))
    monkeypatch.setattr(module, "_openai_chat", call)
    client = module.LLMClient()
    monkeypatch.setattr(client, "_resolve_provider", lambda: ("openai", "https://example.test", "key", "model"))
    with pytest.raises(RuntimeError, match="cancelled"):
        client.chat("system", "user")
    assert call.call_count == 1


@pytest.mark.parametrize("mode", ["complete", "early_close", "error"])
def test_stream_closes_response_and_client_on_all_exit_paths(monkeypatch, mode):
    class Stream(ManagedResource):
        def __iter__(self):
            yield SimpleNamespace(choices=[])  # valid usage-only event
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="a"))])
            if mode == "error":
                raise RuntimeError("broken stream")
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="b"))])

    stream = Stream()
    client = install_client(monkeypatch, lambda **_: stream)
    generator = module._openai_stream_chat("https://example.test", "key", "model", "sys", "user", .6, 10, 1)
    assert next(generator) == "a"
    assert not client.closed and not stream.closed
    if mode == "early_close":
        generator.close()
    elif mode == "error":
        with pytest.raises(RuntimeError, match="broken stream"):
            next(generator)
    else:
        assert list(generator) == ["b"]
    assert stream.closed and client.closed


def test_stream_creation_error_closes_client(monkeypatch):
    def create(**_):
        raise RuntimeError("connection failed")

    client = install_client(monkeypatch, create)
    with pytest.raises(RuntimeError, match="connection failed"):
        next(module._openai_stream_chat("https://example.test", "key", "model", "sys", "user", .6, 10, 1))
    assert client.closed
