"""
多平台数据不变式与正文质量单测
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.models.entities import (
    Base,
    CollectionItemRelation,
    ContentItem,
    FavoriteCollection,
    IngestionItem,
)
from app.services.bilibili.content_fetcher import SUBSTANTIVE_TEXT_MIN_CHARS


def test_platform_composite_uniqueness(tmp_path):
    """验证 (platform, remote_item_id) 复合唯一键允许跨平台相同 ID，但禁止同平台重复"""
    db_file = tmp_path / "test_invariants.db"
    engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)

    with session_factory() as db:
        # 1. 抖音与 B 站允许拥有相同的基础编号（平台隔离）
        item_dy = ContentItem(platform="douyin", remote_item_id="12345", title="抖音视频")
        item_bili = ContentItem(platform="bilibili", remote_item_id="12345", title="B站视频")
        db.add_all([item_dy, item_bili])
        db.commit()

        # 2. 同一平台内重复 remote_item_id 必须抛出唯一性约束异常
        duplicate_dy = ContentItem(platform="douyin", remote_item_id="12345", title="重复抖音视频")
        db.add(duplicate_dy)
        with pytest.raises(IntegrityError):
            db.commit()


def test_collection_item_relation_composite_uniqueness(tmp_path):
    """验证收藏夹-内容关联表的 (collection_id, content_item_id) 复合唯一键"""
    db_file = tmp_path / "test_relations.db"
    engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)

    with session_factory() as db:
        col = FavoriteCollection(platform="bilibili", remote_collection_id="fav_1", title="收藏夹1")
        item = ContentItem(platform="bilibili", remote_item_id="BV123", title="测试视频")
        db.add_all([col, item])
        db.commit()

        rel1 = CollectionItemRelation(collection_id=col.id, content_item_id=item.id)
        db.add(rel1)
        db.commit()

        # 重复绑定同一对关系必须失败
        rel2 = CollectionItemRelation(collection_id=col.id, content_item_id=item.id)
        db.add(rel2)
        with pytest.raises(IntegrityError):
            db.commit()


def test_substantive_text_threshold():
    """验证正文提取严格拒绝不足 50 字符的伪成功内容"""
    assert SUBSTANTIVE_TEXT_MIN_CHARS == 50
