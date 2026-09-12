"""
BUG-02 回归测试：按平台清空入库数据不应影响其它平台

`POST /api/knowledge/clear-all`（`collection_id` 为空/"all"）在
`platform` 指定为单一平台时，只应清空该平台的 Chroma collection 与
`VideoCache`（`IngestionItem`）行，另一平台的向量与数据库状态必须原样保留。
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
from app.models.entities import ContentItem, IngestionItem
from app.services import chroma_service as chroma_module


@pytest.fixture
def db_and_client(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'clear_all.db').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(session_module, "session_factory", factory)

    with factory() as db:
        douyin_item = ContentItem(platform="douyin", remote_item_id="dy1")
        bili_item = ContentItem(platform="bilibili", remote_item_id="BV1")
        db.add_all([douyin_item, bili_item])
        db.flush()
        db.add_all([
            IngestionItem(content_item_id=douyin_item.id, status="done", transcript_text="抖音正文"),
            IngestionItem(content_item_id=bili_item.id, status="done", transcript_text="B站正文"),
        ])
        db.commit()

    fake_chroma = SimpleNamespace(
        clear_platform=Mock(),
        clear_all=Mock(),
    )
    monkeypatch.setattr(chroma_module, "get_chroma_service", lambda: fake_chroma)

    with TestClient(app, headers={"X-Akasha-Client": "1"}) as c:
        yield c, factory, fake_chroma


def test_clearing_one_platform_leaves_the_other_untouched(db_and_client):
    client, factory, fake_chroma = db_and_client

    resp = client.post("/api/knowledge/clear-all", json={"platform": "douyin"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["reset_count"] == 1

    # Only the douyin collection was touched, never the whole store.
    fake_chroma.clear_platform.assert_called_once_with("douyin")
    fake_chroma.clear_all.assert_not_called()

    with factory() as db:
        items = {item.content_item.platform: item for item in db.query(IngestionItem).all()}
        assert items["douyin"].status == "pending"
        assert items["douyin"].transcript_text == ""
        # bilibili row must be completely untouched
        assert items["bilibili"].status == "done"
        assert items["bilibili"].transcript_text == "B站正文"


def test_no_platform_filter_clears_everything(db_and_client):
    client, factory, fake_chroma = db_and_client

    resp = client.post("/api/knowledge/clear-all", json={})
    assert resp.status_code == 200
    assert resp.json()["reset_count"] == 2
    fake_chroma.clear_all.assert_called_once()
    fake_chroma.clear_platform.assert_not_called()

    with factory() as db:
        statuses = {item.status for item in db.query(IngestionItem).all()}
        assert statuses == {"pending"}
