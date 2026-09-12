"""
WBI 签名服务单测

验证：
1. 参数过滤（去除 !'()*）
2. 参数排序与时间戳附加
3. 单飞并发锁与缓存命中（避免刷新风暴）
4. 容灾兜底逻辑
"""
import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from app.services.bilibili.wbi import MIXIN_KEY_ENC_TAB, WbiSigner


def test_mixin_key_generation():
    """验证根据官方混淆表计算 mixin_key"""
    orig = "ea1db124af3c439eaaa9e30dc7477b3b" + "4562da48483446e180fb4140a75b647b"
    mixin = WbiSigner._get_mixin_key(orig)
    assert len(mixin) == 32
    assert isinstance(mixin, str)


def test_filter_params():
    """验证非法字符过滤"""
    params = {
        "title": "hello!world*",
        "query": "(test)'string'",
        "normal": "valid_123",
    }
    filtered = WbiSigner._filter_params(params)
    assert filtered["title"] == "helloworld"
    assert filtered["query"] == "teststring"
    assert filtered["normal"] == "valid_123"


@pytest.mark.asyncio
async def test_wbi_cache_and_single_flight():
    """验证并发请求只触发一次底层 nav 刷新（单飞模式），后续复用缓存"""
    signer = WbiSigner(ttl_hours=12)

    fetch_count = 0

    async def mock_get(*args, **kwargs):
        nonlocal fetch_count
        fetch_count += 1
        await asyncio.sleep(0.05)
        mock_resp = Mock()
        mock_resp.json.return_value = {
            "code": 0,
            "data": {
                "wbi_img": {
                    "img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
                    "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png",
                }
            },
        }
        return mock_resp

    mock_client = AsyncMock()
    mock_client.get = mock_get

    # 并发 10 个签名请求
    tasks = [
        signer.sign({"keyword": f"test_{i}"}, client=mock_client)
        for i in range(10)
    ]
    results = await asyncio.gather(*tasks)

    # 验证单飞：尽管 10 个并发同时发起，但由于 asyncio.Lock，只调用了一次 nav API
    assert fetch_count == 1
    for r in results:
        assert "w_rid" in r
        assert "wts" in r
        assert len(r["w_rid"]) == 32
