import pytest
from fastapi import HTTPException

from app.api.routes import chat


def test_client_keys_must_differ():
    with pytest.raises(ValueError, match="must differ"):
        chat.AskRequest(query="hello", client_keys={"user": "same", "assistant": "same"})


@pytest.mark.asyncio
async def test_non_stream_duplicate_key_is_rejected_before_model_call(monkeypatch):
    called = False

    async def should_not_run(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(chat, "_client_keys_in_use", lambda session_id, keys: True)
    monkeypatch.setattr(chat.rag_service, "answer", should_not_run)

    with pytest.raises(HTTPException) as caught:
        await chat.chat_ask(
            chat.AskRequest(
                query="hello",
                session_id=7,
                client_keys={"user": "u-1", "assistant": "a-1"},
            ),
            db=object(),
        )

    assert caught.value.status_code == 409
    assert called is False
