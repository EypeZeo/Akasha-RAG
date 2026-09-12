"""收藏夹服务：计数口径一致性 + 主动同步强制重抓。"""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app._version import get_version
from app.db.base import Base
from app.models.entities import FavoriteCollection, FavoriteVideo
from app.services.favorites_service import ALL_COLLECTION_ID, favorites_service


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


def test_list_collections_counts_match_actual_rows_not_stored_count(db):
    """真实收藏夹的 video_count 用实际去重行数，绝不 > 全部收藏。"""
    c1 = FavoriteCollection(platform_collection_id="p1", title="财经", video_count=63, is_active=True)
    c2 = FavoriteCollection(platform_collection_id="p2", title="交易", video_count=10, is_active=True)
    db.add_all([c1, c2])
    db.flush()
    # 财经实际只持久化了 2 条（抖音自报 63，两者不一致）
    db.add_all([
        FavoriteVideo(collection_id=c1.id, platform_item_id="v1", title="a", is_active=True),
        FavoriteVideo(collection_id=c1.id, platform_item_id="v2", title="b", is_active=True),
        FavoriteVideo(collection_id=c2.id, platform_item_id="v3", title="c", is_active=True),
    ])
    db.commit()

    items = favorites_service.list_collections(db)
    by_title = {it["title"]: it["video_count"] for it in items}
    all_count = next(it["video_count"] for it in items if it["collection_id"] == ALL_COLLECTION_ID)

    assert all_count == 3
    assert by_title["财经"] == 2   # 实际行数，不是存储列的 63
    assert by_title["交易"] == 1
    assert all(it["video_count"] <= all_count for it in items)  # 子 <= 全部


def test_cursor_pagination_uses_immutable_created_key(db):
    """A later ingestion update must not move an item across list pages."""
    db.add_all([
        FavoriteVideo(platform_item_id=f"cursor-{index}", title=str(index), is_active=True)
        for index in range(3)
    ])
    db.commit()

    first, total, cursor, has_more = favorites_service.list_videos(
        db, ALL_COLLECTION_ID, size=2,
    )
    assert total == 3
    assert has_more is True
    assert cursor

    # updated_at is intentionally irrelevant to the pagination boundary.
    db.query(FavoriteVideo).filter_by(platform_item_id=first[0]["platform_item_id"]).update({"updated_at": datetime(2099, 1, 1)})
    db.commit()
    second, _, _, _ = favorites_service.list_videos(
        db, ALL_COLLECTION_ID, size=2, cursor=cursor,
    )
    assert {item["id"] for item in first}.isdisjoint({item["id"] for item in second})


def test_count_videos_by_kind_uses_duration_not_platform(db):
    """整栏 视频/图文 计数按 duration 判据；零时长条目一律计为图文，与平台无关。"""
    db.add_all([
        FavoriteVideo(platform="douyin", platform_item_id="d-v", title="dv", duration=42, is_active=True),
        FavoriteVideo(platform="douyin", platform_item_id="d-n", title="dn", duration=0, is_active=True),
        # 一个零时长的 B站条目：现实里不该出现，但一旦出现必须计入 note_count 而非被平台判据吞掉
        FavoriteVideo(platform="bilibili", platform_item_id="b-v", title="bv", duration=88, is_active=True),
        FavoriteVideo(platform="bilibili", platform_item_id="b-x", title="bx", duration=0, is_active=True),
    ])
    db.commit()

    v_all, n_all = favorites_service.count_videos_by_kind(db, ALL_COLLECTION_ID)
    assert (v_all, n_all) == (2, 2)

    v_bili, n_bili = favorites_service.count_videos_by_kind(db, ALL_COLLECTION_ID, platform="bilibili")
    assert (v_bili, n_bili) == (1, 1)  # B站也可能有 note，绝不恒为 0


def test_count_videos_by_kind_issues_a_single_query(db):
    """PERF-12: video/note 计数必须是一次条件聚合查询，不是两次独立 COUNT。"""
    db.add_all([
        FavoriteVideo(platform="douyin", platform_item_id=f"v{i}", title=f"t{i}", duration=10, is_active=True)
        for i in range(5)
    ] + [
        FavoriteVideo(platform="douyin", platform_item_id=f"n{i}", title=f"n{i}", duration=0, is_active=True)
        for i in range(3)
    ])
    db.commit()

    engine = db.get_bind()
    query_log = []
    listener = lambda *a: query_log.append(a[2])
    event.listen(engine, "before_cursor_execute", listener)
    try:
        video_count, note_count = favorites_service.count_videos_by_kind(db, ALL_COLLECTION_ID)
    finally:
        event.remove(engine, "before_cursor_execute", listener)

    assert (video_count, note_count) == (5, 3)
    select_queries = [q for q in query_log if q.strip().upper().startswith("SELECT")]
    assert len(select_queries) == 1


@pytest.mark.asyncio
async def test_sync_from_douyin_forces_refetch_by_default(monkeypatch):
    """用户点「同步」→ sync_from_douyin(force=True) → fetch_snapshot(force=True)。"""
    from app.services import favorites_service as mod

    snapshot = SimpleNamespace(collections=[], videos=[])
    fetch = AsyncMock(return_value=snapshot)
    monkeypatch.setattr(mod.collector, "fetch_snapshot", fetch)
    monkeypatch.setattr(mod.favorites_service, "save_snapshot_to_db", lambda db, s: {"added_videos": 0})

    await mod.favorites_service.sync_from_douyin(object())
    assert fetch.await_args.kwargs["force"] is True


def test_get_version_reads_version_txt():
    v = get_version()
    assert v and v[0].isdigit() and "." in v


def test_save_snapshot_differentiates_videos_and_notes(db):
    """验证 save_snapshot_to_db 区分视频 (duration > 0) 与图文 (duration == 0) 的新增与移除统计。"""
    from app.services.douyin_collector import (
        FavoriteScrapedCollection,
        FavoriteScrapedVideo,
        FavoriteScrapeSnapshot,
    )

    col = FavoriteScrapedCollection(platform_collection_id="c1", title="合集", video_count=2)
    v1 = FavoriteScrapedVideo(
        platform_item_id="item_v1", url="http://v1", title="短视频1", author="auth1", duration=35, collection_ids={"c1"}
    )
    n1 = FavoriteScrapedVideo(
        platform_item_id="item_n1", url="http://n1", title="图文笔记1", author="auth2", duration=0, collection_ids={"c1"}
    )
    snap1 = FavoriteScrapeSnapshot(collections=[col], videos=[v1, n1])

    res1 = favorites_service.save_snapshot_to_db(db, snap1)
    assert res1["added_videos"] == 1
    assert res1["added_notes"] == 1
    assert res1["removed_videos"] == 0
    assert res1["removed_notes"] == 0
    assert res1["added_total"] == 2
    assert res1["removed_total"] == 0

    # 第二次快照：移除 v1，保留 n1，新增另一个图文 n2
    n2 = FavoriteScrapedVideo(
        platform_item_id="item_n2", url="http://n2", title="图文笔记2", author="auth3", duration=0, collection_ids={"c1"}
    )
    snap2 = FavoriteScrapeSnapshot(collections=[col], videos=[n1, n2])
    res2 = favorites_service.save_snapshot_to_db(db, snap2)
    assert res2["added_videos"] == 0
    assert res2["added_notes"] == 1
    assert res2["removed_videos"] == 1
    assert res2["removed_notes"] == 0
    assert res2["added_total"] == 1
    assert res2["removed_total"] == 1


@pytest.mark.asyncio
async def test_sync_from_bilibili_tolerates_deleted_videos(db, monkeypatch):
    """验证 B站收藏夹存在已失效视频（fetched < media_count）且 has_more=False 时正常完成同步"""
    from unittest.mock import AsyncMock
    from app.services.bilibili.client import bilibili_client

    monkeypatch.setattr(bilibili_client, "_cookies", {"SESSDATA": "test_sessdata"})
    monkeypatch.setattr(
        bilibili_client,
        "get_user_favorites",
        AsyncMock(return_value=[{"id": 12345, "title": "默认收藏夹", "media_count": 56, "cover": ""}]),
    )

    # 模拟第 1 页 19 条 (有1条失效已删除)，has_more 为 False
    mock_content = {
        "medias": [
            {"bvid": f"BV1xx{i}", "title": f"视频{i}", "duration": 100, "upper": {"name": f"UP{i}"}}
            for i in range(19)
        ],
        "has_more": False,
    }
    monkeypatch.setattr(bilibili_client, "get_favorite_content", AsyncMock(return_value=mock_content))
    monkeypatch.setattr(bilibili_client, "get_video_info", AsyncMock(return_value={"pages": [{"cid": 999, "page": 1, "part": "P1", "duration": 100}]}))

    result = await favorites_service.sync_from_bilibili(db)
    assert result["platform"] == "bilibili"
    assert result["collections_total"] == 1
    assert result["videos_total"] == 19
    assert result["added_videos"] == 19

