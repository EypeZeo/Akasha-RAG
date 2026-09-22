"""Boundaries of the chat-history API and of the client keys the export merges on.

The full-history export pins its pages to a snapshot: `snapshot` reports the newest message id and
the count, `messages?until=` reads up to it. The server only bounds the count (snapshot 413) and the
size of one page (messages 413), measured the way the client measures: UTF-8 bytes of the content
plus of the compact JSON of the sources.
"""
import json
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes import chat
from app.db import session as session_module
from app.db.base import Base
from app.db.session import get_db
from app.models.entities import ChatMessage, ChatSession
from app.services.chat_history import ensure_chat_client_key_column


@pytest.fixture
def api(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    monkeypatch.setattr(session_module, "session_factory", sessionmaker(engine, expire_on_commit=False))
    with Session(engine, expire_on_commit=False) as db:
        app = FastAPI()
        app.include_router(chat.router)
        app.dependency_overrides[get_db] = lambda: db
        with TestClient(app) as client:
            yield db, client
    engine.dispose()


def add_session(db, count=0, **message):
    thread = ChatSession(title="t")
    db.add(thread)
    db.flush()
    rows = [ChatMessage(session_id=thread.id, role="user", content=f"m{i}", **message) for i in range(count)]
    db.add_all(rows)
    db.commit()
    return thread.id, [row.id for row in rows]


def messages(client, session_id, **params):
    return client.get(f"/chat/sessions/{session_id}/messages", params=params)


# --- page size, cursors and the newest-page rule ----------------------------------------------------

def test_limit_is_between_1_and_200_and_defaults_to_200(api):
    db, client = api
    sid, _ = add_session(db, 201)
    assert messages(client, sid, limit=0).status_code == 422
    assert messages(client, sid, limit=201).status_code == 422
    assert len(messages(client, sid).json()["items"]) == 200
    assert len(messages(client, sid, limit=200).json()["items"]) == 200


def test_has_more_is_exact_at_the_page_boundary(api):
    db, client = api
    exact, _ = add_session(db, 5)
    one_more, ids = add_session(db, 6)

    page = messages(client, exact, limit=5).json()
    assert len(page["items"]) == 5 and page["has_more"] is False

    page = messages(client, one_more, limit=5).json()
    assert [m["id"] for m in page["items"]] == ids[1:]  # the newest five, oldest first
    assert page["has_more"] is True


def test_until_is_inclusive_and_before_is_exclusive(api):
    db, client = api
    sid, ids = add_session(db, 5)

    assert [m["id"] for m in messages(client, sid, until=ids[2]).json()["items"]] == ids[:3]
    assert [m["id"] for m in messages(client, sid, before=ids[2]).json()["items"]] == ids[:2]
    both = messages(client, sid, until=ids[4], before=ids[2], limit=10).json()
    assert [m["id"] for m in both["items"]] == ids[:2]
    assert messages(client, sid, until=0).json()["items"] == []
    assert messages(client, sid, until=-1).status_code == 422


def test_an_unknown_session_is_reported_not_raised(api):
    _, client = api
    body = messages(client, 99999).json()
    assert body["success"] is False


# --- byte budget: one page, measured like the client ------------------------------------------------

def add_answer(db, sid, content, sources):
    db.add(ChatMessage(session_id=sid, role="assistant", content=content, retrieved_video_ids=json.dumps(sources)))
    db.commit()


def client_measure(item):
    compact = json.dumps(item["sources"], ensure_ascii=False, separators=(",", ":"))
    return len(item["content"].encode("utf-8")) + len(compact.encode("utf-8"))


def test_a_page_is_measured_in_utf8_bytes_of_content_plus_compact_sources_json(api, monkeypatch):
    db, client = api
    sid, _ = add_session(db)
    source = {"platform": "bilibili", "platform_item_id": "BV1", "title": "标题", "url": "https://example.test/1"}
    add_answer(db, sid, "中文回答", [source])
    item = messages(client, sid).json()["items"][0]
    budget = client_measure(item)
    # JS `JSON.stringify` has no spaces after separators; Python's default would count more.
    assert budget < len(item["content"].encode("utf-8")) + len(json.dumps(item["sources"], ensure_ascii=False).encode("utf-8"))

    monkeypatch.setattr(chat, "MAX_EXPORT_BYTES", budget)
    assert messages(client, sid).status_code == 200
    monkeypatch.setattr(chat, "MAX_EXPORT_BYTES", budget - 1)
    assert messages(client, sid).status_code == 413


def test_the_snapshot_does_not_reject_by_total_bytes(api, monkeypatch):
    db, client = api
    sid, _ = add_session(db, 3)
    monkeypatch.setattr(chat, "MAX_EXPORT_BYTES", 1)

    snapshot = client.get(f"/chat/sessions/{sid}/snapshot")

    assert snapshot.status_code == 200 and snapshot.json()["total"] == 3
    assert messages(client, sid).status_code == 413  # the page is where the byte budget applies


# --- snapshot ---------------------------------------------------------------------------------------

def test_snapshot_reports_the_newest_id_and_the_count(api):
    db, client = api
    sid, ids = add_session(db, 3)
    assert client.get(f"/chat/sessions/{sid}/snapshot").json() == {
        "success": True, "session_id": sid, "snapshot_id": ids[-1], "total": 3,
    }
    empty, _ = add_session(db)
    assert client.get(f"/chat/sessions/{empty}/snapshot").json()["snapshot_id"] == 0


def test_snapshot_rejects_more_than_the_message_limit_and_unknown_sessions(api, monkeypatch):
    db, client = api
    sid, _ = add_session(db, 11)
    monkeypatch.setattr(chat, "MAX_EXPORT_MESSAGES", 11)
    assert client.get(f"/chat/sessions/{sid}/snapshot").status_code == 200
    monkeypatch.setattr(chat, "MAX_EXPORT_MESSAGES", 10)
    assert client.get(f"/chat/sessions/{sid}/snapshot").status_code == 413
    assert client.get("/chat/sessions/99999/snapshot").status_code == 404


# --- client keys ------------------------------------------------------------------------------------

@pytest.fixture
def stream(api, monkeypatch):
    """The stream route with the model call replaced: what matters is whether it is reached."""
    db, client = api
    answer_stream = Mock(side_effect=lambda *a, **kw: iter([("done", {})]))
    monkeypatch.setattr(chat.rag_service, "answer_stream", answer_stream)

    def post(session_id, user, assistant):
        keys = {"user": user, "assistant": assistant}
        return client.post("/chat/ask/stream", json={"query": "q", "session_id": session_id, "client_keys": keys})

    return db, post, answer_stream


def test_the_user_and_assistant_keys_must_differ(stream):
    db, post, answer_stream = stream
    sid, _ = add_session(db)

    assert post(sid, "same", "same").status_code == 422
    answer_stream.assert_not_called()


def test_a_key_already_used_in_the_session_is_a_409_before_the_model_is_called(stream):
    db, post, answer_stream = stream
    sid, _ = add_session(db, 1, client_key="k-user")

    for user, assistant in (("k-user", "k-new"), ("k-new", "k-user")):
        assert post(sid, user, assistant).status_code == 409
    answer_stream.assert_not_called()


def test_the_same_keys_are_allowed_in_another_session_and_in_a_new_one(stream):
    db, post, answer_stream = stream
    used, _ = add_session(db, 1, client_key="k-user")
    other, _ = add_session(db)

    assert post(other, "k-user", "k-asst").status_code == 200
    assert post(None, "k-user", "k-asst").status_code == 200
    assert answer_stream.call_count == 2
    assert answer_stream.call_args.kwargs["client_keys"] == {"user": "k-user", "assistant": "k-asst"}
    assert used != other


def test_the_database_refuses_a_duplicate_key_in_a_session(api):
    db, _ = api
    sid, _ = add_session(db, 1, client_key="dup")
    other, _ = add_session(db)

    db.add(ChatMessage(session_id=other, role="user", content="other session", client_key="dup"))
    db.add_all([ChatMessage(session_id=sid, role="user", content="a"), ChatMessage(session_id=sid, role="user", content="b")])
    db.commit()  # the same key in another session, and any number of rows without a key, are fine

    db.add(ChatMessage(session_id=sid, role="assistant", content="again", client_key="dup"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# --- migration --------------------------------------------------------------------------------------

def test_client_key_migration_adds_the_column_and_the_unique_index_and_is_idempotent():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE chat_messages (id INTEGER PRIMARY KEY, session_id INTEGER, content TEXT)")
        connection.exec_driver_sql("INSERT INTO chat_messages (session_id, content) VALUES (1, 'legacy'), (1, 'legacy 2')")
    ensure_chat_client_key_column(engine)
    ensure_chat_client_key_column(engine)

    with engine.begin() as connection:
        rows = connection.exec_driver_sql("SELECT content, client_key FROM chat_messages ORDER BY id").all()
        assert rows == [("legacy", None), ("legacy 2", None)]  # untouched, and several NULL keys coexist
        connection.exec_driver_sql("INSERT INTO chat_messages (session_id, content, client_key) VALUES (1, 'new', 'k')")
        connection.exec_driver_sql("INSERT INTO chat_messages (session_id, content, client_key) VALUES (2, 'other', 'k')")
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.exec_driver_sql("INSERT INTO chat_messages (session_id, content, client_key) VALUES (1, 'dup', 'k')")
    engine.dispose()
