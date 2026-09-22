import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.entities import CollectionItemRelation, ContentItem, FavoriteCollection, IngestionItem
from app.services.batch_export_service import BatchExportService
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
