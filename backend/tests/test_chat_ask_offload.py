"""
PR2B-3 回归测试：/ask 的同步 rag_service.answer 调用必须挪出事件循环

`chat_ask` 之前直接同步调用 `rag_service.answer(...)`（含检索、最多 8 次
压缩 LLM 调用、最终 LLM 调用、DB 写入），全程阻塞事件循环。用同一套
Event 驱动的"阻塞函数卡住时事件循环是否还在转"模式验证。
"""
from __future__ import annotations

import asyncio
import threading

import pytest

from app.api.routes import chat as chat_module


async def _assert_loop_not_blocked_while(block_started: threading.Event, release_gate: threading.Event) -> None:
    ticks = 0

    async def ticker():
        nonlocal ticks
        while not release_gate.is_set():
            ticks += 1
            await asyncio.sleep(0)

    ticker_task = asyncio.create_task(ticker())
    await asyncio.get_running_loop().run_in_executor(None, block_started.wait, 5)
    for _ in range(5):
        await asyncio.sleep(0)
    assert ticks > 0, "event loop did not tick while rag_service.answer was in flight"
    release_gate.set()
    await ticker_task


@pytest.mark.asyncio
async def test_ask_offloads_rag_answer(monkeypatch):
    started = threading.Event()
    gate = threading.Event()

    def fake_answer(db, query, session_id, collection_id, platform=None, client_keys=None):
        started.set()
        gate.wait(timeout=5)
        return {"answer": "ok", "session_id": 1, "route_type": "chitchat", "sources": []}

    monkeypatch.setattr(chat_module.rag_service, "answer", fake_answer)

    body = chat_module.AskRequest(query="你好")
    route_task = asyncio.create_task(chat_module.chat_ask(body, db=object()))
    await _assert_loop_not_blocked_while(started, gate)
    result = await route_task
    assert result["success"] is True
    assert result["answer"] == "ok"


@pytest.mark.asyncio
async def test_ask_forwards_client_keys_to_non_stream_answer(monkeypatch):
    captured = {}

    def fake_answer(db, query, session_id, collection_id, platform=None, client_keys=None):
        captured["client_keys"] = client_keys
        return {"answer": "ok", "session_id": 1, "route_type": "direct", "sources": []}

    monkeypatch.setattr(chat_module.rag_service, "answer", fake_answer)
    result = await chat_module.chat_ask(
        chat_module.AskRequest(query="hello", client_keys={"user": "u-1", "assistant": "a-1"}),
        db=object(),
    )

    assert result["success"] is True
    assert captured["client_keys"] == {"user": "u-1", "assistant": "a-1"}
