"""
PR2B-3 回归测试：clear-all 的 Chroma/DB 清空工作必须挪出事件循环

`_clear_all_knowledge_sync` 之前直接内联跑在 `async def` 路由里；用同一套
"阻塞函数卡住时事件循环是否还在转" 的 Event 驱动模式验证已经挪到线程池。
"""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.api.routes import knowledge as knowledge_module
from app.db.base import Base
from app.services import chroma_service as chroma_module
from app.services.worker import Worker


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
    assert ticks > 0, "event loop did not tick while clear-all's sync work was in flight"
    release_gate.set()
    await ticker_task


@pytest.mark.asyncio
async def test_clear_all_offloads_chroma_and_db_work(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'clear_all_offload.db').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)

    started = threading.Event()
    gate = threading.Event()

    def fake_clear_all():
        started.set()
        gate.wait(timeout=5)

    fake_chroma = SimpleNamespace(clear_all=Mock(side_effect=fake_clear_all), clear_platform=Mock())
    monkeypatch.setattr(chroma_module, "get_chroma_service", lambda: fake_chroma)
    monkeypatch.setattr(knowledge_module, "worker", Worker())

    with factory() as db:
        route_task = asyncio.create_task(
            knowledge_module.clear_all_knowledge(knowledge_module.ClearAllRequest(), db)
        )
        await _assert_loop_not_blocked_while(started, gate)
        result = await route_task

    assert result == {"success": True, "reset_count": 0}
    fake_chroma.clear_all.assert_called_once()
