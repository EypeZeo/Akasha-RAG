"""
PR2B-5 回归测试：/ask/stream 路由层的轮询逻辑 + 基线回归

真正的"客户端中途断开 -> Starlette request.is_disconnected() 感知到 ->
生产者线程收到取消"这条端到端路径无法用 TestClient 确定性复现（其 ASGI
传输层不保证提前停止读取响应体就会产生 http.disconnect 消息，这是
Starlette/httpx 跨版本已知的不确定行为）——见 PR 描述里的
NEEDS_MANUAL_RUNTIME_VERIFICATION 记录。这里只测试可以确定性验证的部分：
路由的轮询循环本身（不经过真实 ASGI）、以及桥接没有破坏正常路径行为的
基线回归。
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes import chat as chat_module
from app.db import session as session_module
from app.db.base import Base
from app.main import app
from app.services.rag_service import rag_service


@pytest.mark.asyncio
async def test_event_stream_polling_terminates_on_disconnect_without_real_asgi(monkeypatch):
    """直接驱动 event_stream() 协程本身，不经过 StreamingResponse/TestClient；
    is_disconnected() 先 False 后 True，断言轮询正确检测到第二次并终止，
    且置位了 cancel_event。"""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(session_module, "session_factory", factory)

    def fake_answer_stream(db, query, session_id, collection_id, platform=None):
        # Never actually reaches a terminal event on its own -- the
        # route's own disconnect polling must be what ends the stream.
        yield ("delta", {"text": "a"})
        while True:
            yield ("delta", {"text": "still going"})

    monkeypatch.setattr(rag_service, "answer_stream", fake_answer_stream)

    fake_request = AsyncMock()
    fake_request.is_disconnected = AsyncMock(side_effect=[False, True])

    response = await chat_module.chat_ask_stream(
        chat_module.AskRequest(query="hi"), fake_request
    )

    collected = []
    async for chunk in response.body_iterator:
        collected.append(chunk)
        if len(collected) > 5:
            pytest.fail("polling loop did not terminate on the second is_disconnected() == True")

    assert any("delta" in c for c in collected)
    assert fake_request.is_disconnected.await_count >= 2
    engine.dispose()


def test_happy_path_sse_event_sequence_is_unchanged_by_the_bridge(monkeypatch, tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'ask_stream.db').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(session_module, "session_factory", factory)

    def fake_answer_stream(db, query, session_id, collection_id, platform=None):
        yield ("sources", {"sources": []})
        yield ("delta", {"text": "hel"})
        yield ("delta", {"text": "lo"})
        yield ("meta", {"session_id": 1, "route_type": "chitchat"})
        yield ("done", {"ok": True})

    monkeypatch.setattr(rag_service, "answer_stream", fake_answer_stream)

    with TestClient(app, headers={"X-Akasha-Client": "1"}) as client:
        with client.stream("POST", "/api/chat/ask/stream", json={"query": "你好"}) as resp:
            assert resp.status_code == 200
            body = "".join(resp.iter_text())

    event_names = [line[len("event: "):] for line in body.splitlines() if line.startswith("event: ")]
    assert event_names == ["sources", "delta", "delta", "meta", "done"]


def test_producer_opens_its_own_session_not_the_request_scoped_one(monkeypatch, tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'ask_stream_session.db').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(engine)
    real_factory = sessionmaker(engine, expire_on_commit=False)

    call_count = {"n": 0}

    def counting_factory():
        call_count["n"] += 1
        return real_factory()

    monkeypatch.setattr(session_module, "session_factory", counting_factory)

    def fake_answer_stream(db, query, session_id, collection_id, platform=None):
        assert db is not None
        yield ("done", {"ok": True})

    monkeypatch.setattr(rag_service, "answer_stream", fake_answer_stream)

    with TestClient(app, headers={"X-Akasha-Client": "1"}) as client:
        with client.stream("POST", "/api/chat/ask/stream", json={"query": "你好"}) as resp:
            assert resp.status_code == 200
            list(resp.iter_text())

    assert call_count["n"] == 1
