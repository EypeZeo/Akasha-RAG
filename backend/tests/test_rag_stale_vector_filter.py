"""
BUG-19 回归测试：检索必须排除"整体尚未 done"的条目残留在 Chroma 里的向量

多 P 视频某一分 P 转写失败后，之前已成功的分 P 向量仍然留在 Chroma 里可被
检索，但这个 content_item_id 的整体入库状态是 failed（不是 done）——RAG
不该把它当作可信内容返回给用户。
"""
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.entities import ContentItem, IngestionItem
from app.services.rag_service import RagService


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(engine)
    with factory() as db:
        yield db


def _seed(db, *, remote_item_id: str, status: str) -> int:
    item = ContentItem(platform="bilibili", remote_item_id=remote_item_id)
    db.add(item)
    db.flush()
    db.add(IngestionItem(content_item_id=item.id, status=status, transcript_text="text"))
    db.commit()
    return item.id


def test_hits_from_a_not_done_item_are_dropped(db_session):
    failed_id = _seed(db_session, remote_item_id="BVfailed", status="failed")
    done_id = _seed(db_session, remote_item_id="BVdone", status="done")

    hits = [
        {"platform_item_id": "BVfailed", "content_item_id": failed_id, "chunk_id": "BVfailed:1001:0"},
        {"platform_item_id": "BVdone", "content_item_id": done_id, "chunk_id": "BVdone:0"},
    ]

    filtered = RagService._filter_hits_to_done_items(db_session, hits)

    assert [h["content_item_id"] for h in filtered] == [done_id]


def test_legacy_hits_without_a_content_item_id_pass_through_unfiltered(db_session):
    """content_item_id 为 0（upsert 时的历史/未知哨兵值）或缺失时，不能被
    误判成"对应不上任何 VideoCache 行"而整体过滤掉。"""
    hits = [
        {"platform_item_id": "legacy1", "content_item_id": 0},
        {"platform_item_id": "legacy2"},
    ]

    filtered = RagService._filter_hits_to_done_items(db_session, hits)

    assert filtered == hits


def test_no_hits_is_a_noop(db_session):
    assert RagService._filter_hits_to_done_items(db_session, []) == []
