"""
DATA-01 回归测试：同步中途异常必须回滚，不能提交半成品快照

`sync_favorites` 的 `except` 块此前只返回错误响应、不 `db.rollback()`；
由于异常被吞掉、从未传到 `get_db`，`get_db` 的 try/except/else 会走到
"没有异常 → 提交" 的分支，把同步中途 flush 过的半成品数据一起提交。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db import session as session_module
from app.main import app
from app.models.entities import ContentItem
from app.services.favorites_service import favorites_service


@pytest.fixture
def db_and_client(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'sync_rollback.db').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(session_module, "session_factory", factory)

    with TestClient(app, headers={"X-Akasha-Client": "1"}) as c:
        yield c, factory


async def _flush_then_blow_up(db, force=True):
    """模拟真实同步流程中途失败：先 flush 了一部分变更，随后抛异常。"""
    db.add(ContentItem(platform="douyin", remote_item_id="half-written"))
    db.flush()
    raise RuntimeError("模拟同步中途网络异常")


def test_sync_failure_rolls_back_partial_writes(db_and_client, monkeypatch):
    client, factory = db_and_client
    monkeypatch.setattr(favorites_service, "sync_from_douyin", _flush_then_blow_up)

    resp = client.post("/api/favorites/sync", params={"platform": "douyin"})

    assert resp.status_code == 200
    assert resp.json()["success"] is False

    with factory() as db:
        # The flush()ed row must not have survived — get_db must not have
        # committed it after the route swallowed the exception.
        assert db.query(ContentItem).count() == 0


async def _bilibili_success(db):
    """模拟真实同步：正常完成并像 save_snapshot_to_db 一样自行 commit。"""
    db.add(ContentItem(platform="bilibili", remote_item_id="bili-ok"))
    db.commit()
    return {"videos_total": 1, "invalid_count": 0, "added_videos": 1, "removed_videos": 0}


def test_all_platform_sync_does_not_leak_failed_platforms_partial_write(db_and_client, monkeypatch):
    """
    platform=all 时两个平台共用同一个 db session。抖音 flush 后抛异常，
    B 站随后成功并 commit——如果抖音那次失败没有立刻 rollback，B 站的
    commit 会把抖音的半成品一起提交上去（这正是 GPT 复核实测到的问题）。
    """
    client, factory = db_and_client
    monkeypatch.setattr(favorites_service, "sync_from_douyin", _flush_then_blow_up)
    monkeypatch.setattr(favorites_service, "sync_from_bilibili", _bilibili_success)

    resp = client.post("/api/favorites/sync", params={"platform": "all"})

    assert resp.status_code == 200
    assert resp.json()["success"] is True

    with factory() as db:
        items = db.query(ContentItem).all()
        # Only bilibili's committed row should exist; douyin's flushed-then
        # -abandoned row must not have survived B站's later commit.
        assert [item.remote_item_id for item in items] == ["bili-ok"]
