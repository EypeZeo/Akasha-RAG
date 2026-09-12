from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import llm_service as module


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(module.settings, "dashscope_api_key", "fixture-secret")
    return module.EmbeddingClient()


def test_long_document_batches_by_ten_and_restores_response_order(client, monkeypatch):
    calls = []

    def call(**kwargs):
        batch = kwargs["input"]
        assert len(batch) <= 10
        assert kwargs["api_key"] == "fixture-secret"
        assert kwargs["request_timeout"] == 30
        calls.append(batch)
        return SimpleNamespace(status_code=200, output={"embeddings": [
            {"text_index": i, "embedding": [float(text), 1.0]}
            for i, text in reversed(list(enumerate(batch)))]})

    monkeypatch.setattr(module.TextEmbedding, "call", call)
    vectors = client.embed_texts([str(i) for i in range(23)])
    assert [len(batch) for batch in calls] == [10, 10, 3]
    assert [vector[0] for vector in vectors] == list(range(23))


@pytest.mark.parametrize("items", [
    [], [{"text_index": 1, "embedding": [1.0]}],
    [{"text_index": 0, "embedding": [float("nan")]}],
    [{"text_index": 0, "embedding": []}],
])
def test_malformed_vectors_fail_without_repeated_billing(client, monkeypatch, items):
    call = Mock(return_value=SimpleNamespace(status_code=200, output={"embeddings": items}))
    monkeypatch.setattr(module.TextEmbedding, "call", call)
    with pytest.raises(RuntimeError):
        client.embed_texts(["text"])
    assert call.call_count == 1


def test_permanent_api_error_is_not_retried_and_key_is_redacted(client, monkeypatch):
    call = Mock(return_value=SimpleNamespace(status_code=400, message="invalid fixture-secret"))
    monkeypatch.setattr(module.TextEmbedding, "call", call)
    with pytest.raises(RuntimeError, match="status=400") as error:
        client.embed_texts(["text"])
    assert "fixture-secret" not in str(error.value)
    assert call.call_count == 1
