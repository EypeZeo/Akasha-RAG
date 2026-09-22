import json
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes import chat
from app.db.base import Base
from app.db.session import get_db
from app.models.entities import ChatMessage, ChatSession
from app.services.chat_history import ensure_chat_client_key_column
from app.services import rag_service as rag_module


@pytest.fixture
def history():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        thread = ChatSession(title="history")
        db.add(thread)
        db.flush()
        db.add_all([ChatMessage(session_id=thread.id, role="user", content=f"m{i}") for i in range(401)])
        db.commit()
        app = FastAPI()
        app.include_router(chat.router)
        app.dependency_overrides[get_db] = lambda: db
        with TestClient(app) as client:
            yield db, thread.id, client
    engine.dispose()


def test_http_snapshot_is_bounded_and_excludes_later_messages(history):
    db, sid, client = history
    snapshot = client.get(f"/chat/sessions/{sid}/snapshot").json()
    assert snapshot["total"] == 401
    db.add(ChatMessage(session_id=sid, role="assistant", content="later"))
    db.commit()
    items = []
    params = {"until": snapshot["snapshot_id"], "limit": 200}
    while True:
        page = client.get(f"/chat/sessions/{sid}/messages", params=params).json()
        assert len(page["items"]) <= 200
        items = page["items"] + items
        if not page["has_more"]:
            break
        params["before"] = page["items"][0]["id"]
    assert len(items) == 401
    assert [m["content"] for m in items] == [f"m{i}" for i in range(401)]
    assert client.get(f"/chat/sessions/{sid}/messages", params={"limit": 201}).status_code == 422
    assert len(client.get(f"/chat/sessions/{sid}/messages").json()["items"]) == 200


def test_http_snapshot_rejects_oversized_history_and_missing_session(history, monkeypatch):
    _, sid, client = history
    monkeypatch.setattr(chat, "MAX_EXPORT_MESSAGES", 10)
    assert client.get(f"/chat/sessions/{sid}/snapshot").status_code == 413
    assert client.get("/chat/sessions/99999/snapshot").status_code == 404


def test_sources_are_unique_and_unknown_scores_stay_unknown(history):
    db, sid, _ = history
    source = {"platform": "bilibili", "platform_item_id": "BV1", "title": "title"}
    db.add(ChatMessage(session_id=sid, role="assistant", content="answer", retrieved_video_ids=json.dumps([source, source])))
    db.commit()
    result = rag_module.rag_service.get_messages(db, sid, limit=1)[0]
    assert len(result["sources"]) == 1
    assert "score" not in result["sources"][0]


def test_stream_persists_client_identity_and_real_source_score(history, monkeypatch):
    db, sid, _ = history
    service = rag_module.RagService()
    monkeypatch.setattr(rag_module, "get_chroma_service", lambda: Mock(count=lambda: 1))
    hit = {"platform": "douyin", "platform_item_id": "one", "chunk_id": "one:0", "title": "one", "text": "one", "score": 0.42}
    monkeypatch.setattr(service, "_retrieve_hits_for_route", lambda *a, **kw: [hit, hit])
    monkeypatch.setattr(rag_module.llm_client, "stream_chat", lambda **kw: (value for value in ["answer"]))
    list(service.answer_stream(db, "question", sid, client_keys={"user": "u-1", "assistant": "a-1"}))
    latest = service.get_messages(db, sid, limit=2)
    assert [m["client_key"] for m in latest] == ["u-1", "a-1"]
    assert len(latest[1]["sources"]) == 1
    assert latest[1]["sources"][0]["score"] == 0.42


def test_client_key_migration_preserves_legacy_rows_and_is_idempotent():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE chat_messages (id INTEGER PRIMARY KEY, session_id INTEGER, content TEXT)")
        connection.exec_driver_sql("INSERT INTO chat_messages (content) VALUES ('legacy')")
    ensure_chat_client_key_column(engine)
    ensure_chat_client_key_column(engine)
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT content, client_key FROM chat_messages").one() == ("legacy", None)
    engine.dispose()
