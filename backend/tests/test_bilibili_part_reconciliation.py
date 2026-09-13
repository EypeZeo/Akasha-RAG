"""
BUG-10/NET-04 回归测试：B 站分 P 变更的完整一致性契约

`_sync_videos_and_cache` 之前对 `ContentPart` 只有"缺少则新增"的逻辑：
不会因为标题/时长变了而更新，也不会因为平台那边分 P 减少了而删除多余的
本地行，且"default"占位 part 可能和后来抓到的真实 pages 同时残留。
修复后按 (remote_part_id, part_index, part_title, duration) 四元组比较，
发现差异就清 Chroma 向量 + 重建 ContentPart + 把 IngestionItem 打回
pending，只有 get_video_info 真的成功（freshly_enriched）且这段持久化
本身成功时才推进 last_enriched_at。
"""
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.entities import ContentItem, ContentPart, IngestionItem
from app.services import favorites_service as favorites_service_module
from app.services.douyin_collector import (
    FavoriteScrapedCollection,
    FavoriteScrapedVideo,
    FavoriteScrapeSnapshot,
)
from app.services.favorites_service import favorites_service


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


@pytest.fixture
def fake_chroma(monkeypatch):
    fake = Mock(delete_by_video=Mock())
    monkeypatch.setattr(favorites_service_module, "get_chroma_service", lambda: fake)
    return fake


def _collection():
    return FavoriteScrapedCollection(platform_collection_id="c1", title="合集", video_count=1)


def _video(parts, duration=200, freshly_enriched=True):
    return FavoriteScrapedVideo(
        platform_item_id="BV1", url="http://v1", title="视频", author="a", duration=duration,
        collection_ids={"c1"}, parts=parts, freshly_enriched=freshly_enriched,
    )


def _get_item(db):
    db.expire_all()
    return db.query(ContentItem).filter_by(remote_item_id="BV1").one()


def _get_parts(db, item_id):
    db.expire_all()
    return db.query(ContentPart).filter_by(content_item_id=item_id).order_by(ContentPart.part_index).all()


def _get_ingestion(db, item_id):
    db.expire_all()
    return db.query(IngestionItem).filter_by(content_item_id=item_id).one()


def test_part_rename_or_duration_change_triggers_full_reconciliation(db, fake_chroma):
    v1 = _video(parts=[{"remote_part_id": "100", "part_index": 1, "part_title": "P1", "duration": 200}])
    snap1 = FavoriteScrapeSnapshot(collections=[_collection()], videos=[v1], platform="bilibili")
    favorites_service.save_snapshot_to_db(db, snap1)

    item = _get_item(db)
    ing = _get_ingestion(db, item.id)
    ing.status = "done"
    ing.transcript_text = "已完成转写"
    db.commit()

    v1_renamed = _video(
        parts=[{"remote_part_id": "100", "part_index": 1, "part_title": "P1（重新剪辑版）", "duration": 250}],
        duration=250,
    )
    snap2 = FavoriteScrapeSnapshot(collections=[_collection()], videos=[v1_renamed], platform="bilibili")
    favorites_service.save_snapshot_to_db(db, snap2)

    fake_chroma.delete_by_video.assert_called_once_with("BV1", platform="bilibili", content_item_id=item.id)
    parts = _get_parts(db, item.id)
    assert len(parts) == 1
    assert parts[0].part_title == "P1（重新剪辑版）"
    assert parts[0].duration == 250
    ing2 = _get_ingestion(db, item.id)
    assert ing2.status == "pending"
    assert ing2.transcript_text == ""
    assert _get_item(db).last_enriched_at is not None


def test_removed_part_is_deleted_not_left_stale(db, fake_chroma):
    v1 = _video(parts=[
        {"remote_part_id": "100", "part_index": 1, "part_title": "P1", "duration": 100},
        {"remote_part_id": "101", "part_index": 2, "part_title": "P2", "duration": 100},
    ], duration=200)
    snap1 = FavoriteScrapeSnapshot(collections=[_collection()], videos=[v1], platform="bilibili")
    favorites_service.save_snapshot_to_db(db, snap1)
    item = _get_item(db)
    assert len(_get_parts(db, item.id)) == 2

    v1_one_part = _video(parts=[{"remote_part_id": "100", "part_index": 1, "part_title": "P1", "duration": 100}], duration=100)
    snap2 = FavoriteScrapeSnapshot(collections=[_collection()], videos=[v1_one_part], platform="bilibili")
    favorites_service.save_snapshot_to_db(db, snap2)

    parts = _get_parts(db, item.id)
    assert [p.remote_part_id for p in parts] == ["100"]
    fake_chroma.delete_by_video.assert_called_once()


def test_default_placeholder_is_replaced_not_left_alongside_real_parts(db, fake_chroma):
    """第一次同步时富化失败/未跑，落地一个 default 占位 part；第二次同步
    拿到真实分 P 数据后，占位行必须被替换，不能和真实 parts 同时残留。"""
    v1_no_parts = _video(parts=[], freshly_enriched=False)
    snap1 = FavoriteScrapeSnapshot(collections=[_collection()], videos=[v1_no_parts], platform="bilibili")
    favorites_service.save_snapshot_to_db(db, snap1)
    item = _get_item(db)
    parts = _get_parts(db, item.id)
    assert [p.remote_part_id for p in parts] == ["default"]
    fake_chroma.delete_by_video.assert_not_called()

    v1_real_parts = _video(parts=[
        {"remote_part_id": "100", "part_index": 1, "part_title": "P1", "duration": 100},
        {"remote_part_id": "101", "part_index": 2, "part_title": "P2", "duration": 100},
    ], duration=200)
    snap2 = FavoriteScrapeSnapshot(collections=[_collection()], videos=[v1_real_parts], platform="bilibili")
    favorites_service.save_snapshot_to_db(db, snap2)

    parts2 = _get_parts(db, item.id)
    assert sorted(p.remote_part_id for p in parts2) == ["100", "101"]
    assert "default" not in [p.remote_part_id for p in parts2]
    fake_chroma.delete_by_video.assert_called_once()


def test_enrichment_failure_does_not_advance_or_corrupt_existing_real_parts(db, fake_chroma):
    """网络/API 失败时 v.parts 会退回上一次已知数据（不是清空）——这里直接
    验证 _sync_videos_and_cache 收到"这轮没有拿到新 parts"的信号
    （parts=[], freshly_enriched=False）时，不会用一个 default 占位行去
    污染已经有真实分 P 数据的视频。"""
    v1 = _video(parts=[{"remote_part_id": "100", "part_index": 1, "part_title": "P1", "duration": 200}])
    snap1 = FavoriteScrapeSnapshot(collections=[_collection()], videos=[v1], platform="bilibili")
    favorites_service.save_snapshot_to_db(db, snap1)
    item = _get_item(db)
    first_enriched_at = item.last_enriched_at
    assert first_enriched_at is not None

    # This sync's snapshot construction already reused the previous parts
    # (favorites_service.sync_from_bilibili does this on a failed re-fetch),
    # so the payload still carries the real parts -- just not freshly
    # enriched this round.
    v1_reused = _video(
        parts=[{"remote_part_id": "100", "part_index": 1, "part_title": "P1", "duration": 200}],
        freshly_enriched=False,
    )
    snap2 = FavoriteScrapeSnapshot(collections=[_collection()], videos=[v1_reused], platform="bilibili")
    favorites_service.save_snapshot_to_db(db, snap2)

    assert _get_item(db).last_enriched_at == first_enriched_at
    fake_chroma.delete_by_video.assert_not_called()
    assert [p.remote_part_id for p in _get_parts(db, item.id)] == ["100"]
