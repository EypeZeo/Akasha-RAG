"""
BUG-04 回归测试：B 站客户端的事件循环级缓存不能靠 id(loop) 复用来撞键

`_clients`/`_semaphores`（client.py）与 `_locks`（wbi.py）原本用
`id(asyncio.get_running_loop())` 做字典键——CPython 的 `id()` 是内存地址，
一个已经被垃圾回收的事件循环的地址可能被新创建的循环复用，导致新循环
命中一个绑定在已关闭循环上的 client/信号量/锁。换成 `weakref.WeakKeyDictionary`
（键是循环对象本身）后，循环被 GC 时条目自动清除，不存在这类复用风险。
"""
from __future__ import annotations

import asyncio
import gc
from unittest.mock import AsyncMock
from types import SimpleNamespace

import httpx

import pytest

from app.services.bilibili.client import BilibiliClient
from app.services.bilibili.wbi import WbiSigner


@pytest.fixture
def isolated_client(monkeypatch):
    # 测试关心的是事件循环维度的缓存生命周期，不是真实凭据加载。
    monkeypatch.setattr(BilibiliClient, "_load_state", lambda self: None)
    return BilibiliClient()


def test_get_client_does_not_leak_across_destroyed_loops(isolated_client):
    clients_seen = []

    def _run_and_capture():
        async def _inner():
            c = isolated_client._get_client()
            # Keep the instance alive until the comparison below.  Comparing
            # ``id()`` after the first local has been released is itself
            # flaky: CPython is allowed to reuse that memory address.
            clients_seen.append(c)
        asyncio.run(_inner())

    _run_and_capture()
    gc.collect()  # 确保第一个已销毁的循环被真正回收，触发弱引用清理
    _run_and_capture()

    # 两次拿到的必须是不同的 client 实例（第二个循环不该命中第一个循环
    # 遗留下来的、已经绑定在一个已关闭循环上的 client）。
    assert clients_seen[0] is not clients_seen[1]
    # 弱引用字典应该已经把第一个循环的条目自动清掉，不会无限堆积。
    assert len(isolated_client._clients) <= 1


def test_aclose_closes_every_cached_client_and_clears_caches(isolated_client):
    async def _populate_and_close():
        isolated_client._get_client()
        isolated_client._get_semaphore()
        await isolated_client.aclose()

    asyncio.run(_populate_and_close())

    assert len(isolated_client._clients) == 0
    assert len(isolated_client._semaphores) == 0


def test_aclose_does_not_close_or_evict_another_event_loops_pool(isolated_client):
    first = asyncio.new_event_loop()
    second = asyncio.new_event_loop()

    async def populate():
        client = isolated_client._get_client()
        isolated_client._get_semaphore()
        return client

    try:
        first_client = first.run_until_complete(populate())
        second_client = second.run_until_complete(populate())
        first.run_until_complete(isolated_client.aclose())
        assert first_client.is_closed
        assert not second_client.is_closed
        assert isolated_client._clients[second] is second_client
        assert second in isolated_client._semaphores
        second.run_until_complete(isolated_client.aclose())
        assert second_client.is_closed
    finally:
        first.run_until_complete(isolated_client.aclose())
        second.run_until_complete(isolated_client.aclose())
        first.close()
        second.close()


@pytest.mark.asyncio
async def test_transient_request_failure_does_not_close_shared_pool(isolated_client, monkeypatch):
    response = httpx.Response(200)
    client = SimpleNamespace(
        is_closed=False,
        get=AsyncMock(side_effect=[httpx.ConnectTimeout("timeout"), response]),
        aclose=AsyncMock(),
    )
    loop = asyncio.get_running_loop()
    isolated_client._clients[loop] = client
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    result = await isolated_client._request("GET", "https://api.bilibili.com/test", max_retries=2)
    assert result is response
    assert isolated_client._clients[loop] is client
    assert client.get.await_count == 2
    client.aclose.assert_not_awaited()
    await isolated_client.aclose()
    client.aclose.assert_awaited_once()


def test_wbi_lock_does_not_leak_across_destroyed_loops():
    signer = WbiSigner()
    locks_seen = []

    def _run_and_capture():
        async def _inner():
            # See the matching client test above: retain both locks so this
            # test verifies object identity rather than allocator reuse.
            locks_seen.append(signer._lock_for_current_loop())
        asyncio.run(_inner())

    _run_and_capture()
    gc.collect()
    _run_and_capture()

    assert locks_seen[0] is not locks_seen[1]
    assert len(signer._locks) <= 1
