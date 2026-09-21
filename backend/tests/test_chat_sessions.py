"""Chat session management: rename, title search, message pagination,
and retrieval-scope enforcement on the non-vector routes."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.entities import (
    ChatMessage,
    ChatSession,
    ContentItem,
    IngestionItem,
)
from app.services.rag_service import rag_service


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


def _session_with_messages(db, title, n):
    s = ChatSession(title=title)
    db.add(s)
    db.flush()
    for i in range(n):
        db.add(ChatMessage(session_id=s.id, role="user" if i % 2 == 0 else "assistant", content=f"m{i}"))
    db.commit()
    return s


def test_rename_session(db):
    s = _session_with_messages(db, "New Chat", 1)
    assert rag_service.rename_session(db, s.id, "  RAG 架构讨论  ") is True
    assert db.get(ChatSession, s.id).title == "RAG 架构讨论"
    assert rag_service.rename_session(db, s.id, "   ") is False   # empty -> rejected
    assert rag_service.rename_session(db, 999999, "x") is False   # missing -> rejected


def test_list_sessions_title_search(db):
    _session_with_messages(db, "抖音带货话术复盘", 2)
    _session_with_messages(db, "Bilibili 番剧考据", 2)
    all_rows = rag_service.list_sessions(db)
    assert len(all_rows) == 2
    hit = rag_service.list_sessions(db, q="番剧")
    assert [r["title"] for r in hit] == ["Bilibili 番剧考据"]
    assert rag_service.list_sessions(db, q="不存在的关键词") == []


def test_get_messages_pagination(db):
    s = _session_with_messages(db, "long thread", 10)

    full = rag_service.get_messages(db, s.id)
    assert len(full) == 10
    assert [m["content"] for m in full] == [f"m{i}" for i in range(10)]

    recent = rag_service.get_messages(db, s.id, limit=4)
    assert [m["content"] for m in recent] == ["m6", "m7", "m8", "m9"]  # newest 4, time-ascending

    earlier = rag_service.get_messages(db, s.id, before=recent[0]["id"], limit=4)
    assert [m["content"] for m in earlier] == ["m2", "m3", "m4", "m5"]

    assert rag_service.get_messages(db, 424242) is None


def test_db_routes_respect_platform_and_collection_scope(db):
    """_db_list_context / _db_content_context must honor platform + scope_ids."""
    def add_done(platform, remote_id, title):
        ci = ContentItem(platform=platform, remote_item_id=remote_id, title=title, duration=30, is_active=True)
        db.add(ci)
        db.flush()
        db.add(IngestionItem(content_item_id=ci.id, status="done", transcript_text=f"transcript of {title}"))
        return ci

    add_done("douyin", "dy-1", "抖音视频一")
    add_done("bilibili", "bv-1", "B站视频一")
    add_done("bilibili", "bv-2", "B站视频二")
    db.commit()

    whole = rag_service._db_list_context(db)
    assert "抖音视频一" in whole and "B站视频一" in whole

    only_bili = rag_service._db_list_context(db, platform="bilibili")
    assert "B站视频一" in only_bili and "抖音视频一" not in only_bili

    scoped = rag_service._db_list_context(db, scope_ids={("bilibili", "bv-2")})
    assert scoped.strip() == "- B站视频二 (bv-2)"

    # empty scope must NOT fall back to the whole library
    assert rag_service._db_content_context(db, scope_ids=set()) == ""
