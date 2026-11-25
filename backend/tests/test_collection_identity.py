import asyncio
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import session as db_session
from app.db.base import Base
from app.db.favorites_repair import deactivate_misclassified_douyin_favorites
from app.models.entities import CollectionItemRelation, ContentItem, FavoriteCollection, IngestionItem
from app.services.batch_export_service import BatchExportService
from app.services.douyin_collector import DouyinCollector, FavoriteScrapeSnapshot, collector
from app.services.favorites_service import favorites_service
from app.services.knowledge_service import knowledge_service
from app.services.rag_service import rag_service
from app.services.worker import worker


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def collection(db, platform, remote, local_id):
    row = FavoriteCollection(id=local_id, platform=platform, remote_collection_id=remote, title=remote)
    db.add(row)
    db.flush()
    return row


def content(db, platform, remote, title, collections, status="done"):
    item = ContentItem(platform=platform, remote_item_id=remote, title=title, duration=30)
    db.add(item)
    db.flush()
    db.add(IngestionItem(content_item_id=item.id, status=status, transcript_text=title))
    for col in collections:
        db.add(CollectionItemRelation(collection_id=col.id, content_item_id=item.id))
    db.commit()
    return item


def test_douyin_fallback_reads_only_douyin_with_all_active_memberships(db, monkeypatch):
    first = collection(db, "douyin", "shared-id", 1)
    second = collection(db, "douyin", "second", 2)
    other = collection(db, "bilibili", "shared-id", 3)
    inactive = collection(db, "douyin", "inactive", 4)
    item = content(db, "douyin", "12345", "Douyin work", [first, second, other, inactive])
    content(db, "bilibili", "BV1234567890", "Bilibili work", [other, first])
    inactive.is_active = False
    db.commit()
    monkeypatch.setattr(db_session, "session_factory", sessionmaker(db.get_bind()))

    snapshot = DouyinCollector._snapshot_from_database()

    assert snapshot is not None
    assert snapshot.is_cached and not snapshot.is_complete
    assert {row.platform_collection_id for row in snapshot.collections} == {"shared-id", "second"}
    assert [(row.platform_item_id, row.collection_ids) for row in snapshot.videos] == [
        (item.remote_item_id, {"shared-id", "second"}),
    ]


def test_douyin_sync_keeps_cached_snapshot_read_only(db, monkeypatch):
    cached = FavoriteScrapeSnapshot(is_cached=True, is_complete=False)
    monkeypatch.setattr(collector, "fetch_snapshot", AsyncMock(return_value=cached))

    def unexpected_save(*_):
        pytest.fail("A cached snapshot must not be saved")

    monkeypatch.setattr(favorites_service, "save_snapshot_to_db", unexpected_save)
    with pytest.raises(RuntimeError, match="已保留本地收藏"):
        asyncio.run(favorites_service.sync_from_douyin(db))


def test_repair_deactivates_copies_and_keeps_original_collections(db):
    original_video = collection(db, "bilibili", "bili", 1)
    original_article = collection(db, "zhihu", "zhihu", 2)
    original_empty = collection(db, "zhihu", "empty", 3)
    shared_original = collection(db, "bilibili", "shared-id", 4)
    copied_video = collection(db, "douyin", "bili", 5)
    copied_article = collection(db, "douyin", "zhihu", 6)
    copied_empty = collection(db, "douyin", "empty", 7)
    native_collection = collection(db, "douyin", "shared-id", 8)
    for folder in (copied_video, copied_article, copied_empty, native_collection):
        folder.created_at = datetime(2026, 1, 2)
    native_item = content(db, "douyin", "12345", "Native work", [native_collection])
    video = content(db, "bilibili", "BV1234567890", "Bilibili work", [original_video])
    article = content(db, "zhihu", "zhihu:article:123", "Zhihu work", [original_article])
    video_copy = content(db, "douyin", video.remote_item_id, video.title, [copied_video])
    article_copy = content(db, "douyin", article.remote_item_id, article.title, [copied_article])

    engine = db.get_bind()
    assert deactivate_misclassified_douyin_favorites(engine) == (3, 2)
    assert deactivate_misclassified_douyin_favorites(engine) == (0, 0)
    db.expire_all()
    active_folders = set(db.scalars(select(FavoriteCollection.id).where(FavoriteCollection.is_active)))
    assert active_folders == {
        original_video.id, original_article.id, original_empty.id, shared_original.id, native_collection.id,
    }
    assert set(db.scalars(select(ContentItem.id).where(ContentItem.is_active))) == {
        native_item.id, video.id, article.id,
    }
    assert set(db.scalars(select(IngestionItem.content_item_id))) == {
        native_item.id, video.id, article.id, video_copy.id, article_copy.id,
    }


def test_export_uses_remote_identity_and_all_memberships(db):
    a = collection(db, "douyin", "9001", 1)
    b = collection(db, "douyin", "1", 2)
    content(db, "douyin", "a", "A", [a])
    content(db, "douyin", "b", "B", [b])
    content(db, "douyin", "shared", "AB", [a, b])
    service = BatchExportService()
    assert {item.title for _, item in service.get_exportable_videos(db, "1")} == {"B", "AB"}
    assert {item.title for _, item in service.get_exportable_videos(db, "9001")} == {"A", "AB"}


def test_pending_and_ingest_resolve_collection_with_platform(db, monkeypatch):
    dy = collection(db, "douyin", "same", 1)
    bili = collection(db, "bilibili", "same", 2)
    content(db, "douyin", "dy", "DY", [dy], "pending")
    item = content(db, "bilibili", "bv", "BV", [bili], "pending")
    monkeypatch.setattr(worker, "blocked_platforms", lambda: set())
    submitted = []
    monkeypatch.setattr(worker, "submit", lambda task, fn, ids, **kwargs: submitted.extend(ids))
    pending = knowledge_service.list_pending_items(db, "same", platform="bilibili")
    assert [row["id"] for row in pending["items"]] == [item.id]
    knowledge_service.start_sync(db, collection_id="same", platform="bilibili")
    assert submitted == [item.id]


def test_ambiguous_legacy_collection_fails_instead_of_selecting_first(db):
    collection(db, "douyin", "same", 1)
    collection(db, "bilibili", "same", 2)
    db.commit()
    with pytest.raises(ValueError, match="platform"):
        favorites_service.list_videos(db, "same")
    with pytest.raises(ValueError, match="platform"):
        BatchExportService().get_exportable_videos(db, "same")


def test_rag_collection_scope_keeps_content_platform(db):
    dy = collection(db, "douyin", "only-dy", 1)
    bili = collection(db, "bilibili", "only-bili", 2)
    content(db, "douyin", "same-item", "inside", [dy])
    content(db, "bilibili", "same-item", "outside", [bili])
    scope = rag_service._resolve_collection_scope(db, "only-dy")
    assert "inside" in rag_service._db_list_context(db, scope_ids=scope)
    assert "outside" not in rag_service._db_list_context(db, scope_ids=scope)
