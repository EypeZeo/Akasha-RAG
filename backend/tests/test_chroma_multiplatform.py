"""
Chroma 多平台向量检索与分P隔离单测
"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services.chroma_service import ChromaService


def make_service(collection):
    svc = object.__new__(ChromaService)
    svc._collections = {"douyin": collection, "bilibili": collection}
    return svc


def test_douyin_legacy_chunk_id_format():
    """验证抖音单集/默认仍然沿用 remote_id:idx 兼容格式"""
    collection = SimpleNamespace(
        get=Mock(return_value={"ids": []}),
        upsert=Mock(),
        delete=Mock(),
    )
    svc = make_service(collection)
    ids = svc.upsert_video_chunks(
        platform_item_id="7123456789",
        title="抖音测试视频",
        chunks=["文本块1", "文本块2"],
        embeddings=[[0.1] * 128, [0.2] * 128],
        platform="douyin",
        part_id=0,
    )
    assert ids == ["7123456789:0", "7123456789:1"]


def test_bilibili_multipart_chunk_id_format_and_scoping():
    """验证 B 站多 P 视频采用 platform:remote_id:part_id:idx 格式，且 upsert 限定该 part"""
    collection = SimpleNamespace(
        get=Mock(return_value={"ids": ["bilibili:BV1xx411c7mD:1001:0"]}),
        upsert=Mock(),
        delete=Mock(),
    )
    svc = make_service(collection)
    ids = svc.upsert_video_chunks(
        platform_item_id="BV1xx411c7mD",
        title="B站多P视频 P1",
        chunks=["分P文本1"],
        embeddings=[[0.5] * 128],
        platform="bilibili",
        part_id=1001,
        part_index=0,
    )
    assert ids == ["bilibili:BV1xx411c7mD:1001:0"]
    # 每个平台已独立集合，get 仅需限定 part_id，避免误删其它分P。
    collection.get.assert_called_once()
    get_kw = collection.get.call_args[1]
    assert get_kw["where"] == {
        "$and": [
            {"platform_item_id": "BV1xx411c7mD"},
            {"part_id": 1001},
        ]
    }


def test_search_with_empty_scope_returns_nothing_without_querying():
    """BUG-03: 空 scope（收藏夹存在但没有内容）必须直接返回空，不能退化成全库检索"""
    collection = SimpleNamespace(count=Mock(return_value=10), query=Mock())
    svc = make_service(collection)

    res = svc.search(query_vector=[0.1] * 8, top_k=5, scope_ids=set())

    assert res == []
    collection.query.assert_not_called()
    collection.count.assert_not_called()


def test_search_pre_filtering():
    """验证 search 方法在 Chroma 层注入 where 条件进行前置过滤"""
    collection = SimpleNamespace(
        count=Mock(return_value=10),
        query=Mock(return_value={
            "ids": [["bilibili:BV1xx:0"]],
            "distances": [[0.1]],
            "metadatas": [[{
                "chunk_id": "bilibili:BV1xx:0",
                "platform": "bilibili",
                "platform_item_id": "BV1xx",
                "title": "B站测试",
                "canonical_url": "https://www.bilibili.com/video/BV1xx",
            }]],
            "documents": [["测试正文"]],
        }),
    )
    svc = make_service(collection)

    # 平台过滤
    res = svc.search(
        query_vector=[0.1] * 128,
        top_k=5,
        platform="bilibili",
        use_mmr=False,
    )
    assert len(res) == 1
    assert res[0]["platform"] == "bilibili"
    assert res[0]["canonical_url"] == "https://www.bilibili.com/video/BV1xx"

    # 平台已由集合隔离，where 不再冗余携带 platform。
    collection.query.assert_called_once()
    assert "where" not in collection.query.call_args[1]


def test_clear_platform_only_recreates_the_target_collection():
    """BUG-02: 按平台清空只应删除/重建对应平台的 collection，另一个原样保留"""
    calls = []

    class FakeClient:
        def delete_collection(self, name):
            calls.append(("delete", name))

    svc = object.__new__(ChromaService)
    svc._client = FakeClient()
    bilibili_collection = SimpleNamespace()
    svc._collections = {"douyin": SimpleNamespace(), "bilibili": bilibili_collection}

    created = []

    def fake_create(platform):
        created.append(platform)
        return SimpleNamespace()

    svc._create_collection = fake_create
    svc.clear_platform("douyin")

    assert calls == [("delete", "akasha_douyin")]
    assert created == ["douyin"]
    # bilibili's collection object is untouched (still the same instance)
    assert svc._collections["bilibili"] is bilibili_collection


def test_cross_platform_search_merges_by_score():
    douyin = SimpleNamespace(count=Mock(return_value=1), query=Mock(return_value={
        "ids": [["dy:0"]], "distances": [[0.2]],
        "metadatas": [[{"platform": "douyin", "platform_item_id": "dy", "title": "抖音"}]],
        "documents": [["抖音正文"]],
    }))
    bilibili = SimpleNamespace(count=Mock(return_value=1), query=Mock(return_value={
        "ids": [["bili:0"]], "distances": [[0.1]],
        "metadatas": [[{"platform": "bilibili", "platform_item_id": "BV1", "title": "B站"}]],
        "documents": [["B站正文"]],
    }))
    svc = object.__new__(ChromaService)
    svc._collections = {"douyin": douyin, "bilibili": bilibili}

    results = svc.search([0.1] * 8, top_k=2, use_mmr=False)

    assert [item["platform"] for item in results] == ["bilibili", "douyin"]
