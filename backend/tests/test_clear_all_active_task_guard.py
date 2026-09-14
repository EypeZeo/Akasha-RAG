"""
PR2B-3 回归测试：clear-all 必须在入库任务活跃时拒绝，不能和 worker 抢跑

入库流水线用一次比较并置换的 SQL（`WHERE status == "pending"`）认领条目，
认领之后从不会再检查这条记录是否被外部重置过。如果 `clear-all` 在某条目
已被 worker 认领期间运行，它会把所有条目（不论当前状态）重置回 pending，
随后 worker 线程仍然会把向量写进刚清空重建的 Chroma 集合——DB 说
pending，Chroma 却已经有向量。修复复用 `reset_failed_videos`
（`knowledge.py:118-145`）已经验证过的同一条防线：
`worker.has_active_tasks()` 为真就直接拒绝，两个分支（清空全部/按平台、
清空指定收藏夹）都要覆盖。
"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db import session as session_module
from app.main import app
from app.models.entities import ContentItem, FavoriteCollection, CollectionItemRelation, IngestionItem
from app.services import chroma_service as chroma_module
from app.api.routes import knowledge as knowledge_module
from app.services.worker import Worker


@pytest.fixture
def db_and_client(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'clear_all_guard.db').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(session_module, "session_factory", factory)

    with factory() as db:
        item = ContentItem(platform="douyin", remote_item_id="dy1")
        collection = FavoriteCollection(platform_collection_id="c1", title="合集")
        db.add_all([item, collection])
        db.flush()
        db.add(CollectionItemRelation(collection_id=collection.id, content_item_id=item.id, is_active=True))
        db.add(IngestionItem(content_item_id=item.id, status="done", transcript_text="正文"))
        db.commit()

    fake_chroma = SimpleNamespace(
        clear_platform=Mock(), clear_all=Mock(), delete_by_video=Mock(),
    )
    monkeypatch.setattr(chroma_module, "get_chroma_service", lambda: fake_chroma)

    active_worker = Worker()
    active_worker.submit("active-ingestion", lambda: None)
    monkeypatch.setattr(knowledge_module, "worker", active_worker)

    with TestClient(app, headers={"X-Akasha-Client": "1"}) as c:
        yield c, factory, fake_chroma, collection.remote_collection_id or "c1"


def test_clear_all_refuses_while_worker_has_active_tasks(db_and_client):
    client, factory, fake_chroma, _ = db_and_client

    resp = client.post("/api/knowledge/clear-all", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert body["chroma_cleared"] is False

    fake_chroma.clear_all.assert_not_called()
    fake_chroma.clear_platform.assert_not_called()
    with factory() as db:
        assert db.query(IngestionItem).one().status == "done"


def test_clear_all_scoped_to_collection_also_refuses_while_active(db_and_client):
    client, factory, fake_chroma, collection_remote_id = db_and_client

    resp = client.post("/api/knowledge/clear-all", json={"collection_id": collection_remote_id})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False

    fake_chroma.delete_by_video.assert_not_called()
    with factory() as db:
        assert db.query(IngestionItem).one().status == "done"
