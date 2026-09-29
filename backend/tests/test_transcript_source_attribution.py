"""``content_parts.transcript_source`` 的归因契约。

被修掉的缺陷（"系统性撒谎"）：
``favorites_service`` 在**任何正文存在之前**就把每一行 ``ContentPart`` 写成
``transcript_source="whisper_asr"``；``entities.py`` 的列默认值和
``db/migration.py`` 的建表默认值同值；全仓库没有任何代码写别的值。于是 B 站
一条由**字幕**得到正文的分 P，被永久标记成 Whisper ASR（而 ASR 引擎实际是
DashScope paraformer，"whisper" 本身也是错的）。

本文件锁定的新契约：

1. 创建 ``ContentPart`` 是"计划"，不是"正文"——创建路径不得声称任何来源；
2. 真实性在正文产生处写入；当前 B 站抓取器不暴露它走的是字幕还是 ASR，
   所以只能写"来源未知"，不能猜；
3. ``IngestionItem`` 的自动补建 ContentItem 钩子不得为未知平台伪造抖音记录
   （``platform="douyin"`` + ``https://www.douyin.com/video/{id}``）。
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import session as session_module
from app.db.base import Base
from app.models.entities import (
    ContentItem,
    ContentPart,
    IngestionItem,
    TRANSCRIPT_SOURCE_UNKNOWN,
    TRANSCRIPT_SOURCE_UNSET,
)
from app.services import content_kind
from app.services import favorites_service as favorites_service_module
from app.services import knowledge_service as knowledge_module
from app.services import media_service as media_module
from app.services.bilibili.client import bilibili_client
from app.services.bilibili.content_fetcher import bilibili_content_fetcher
from app.services.douyin_collector import (
    FavoriteScrapedCollection,
    FavoriteScrapedVideo,
    FavoriteScrapeSnapshot,
)
from app.services.favorites_service import favorites_service
from app.services.worker import Worker

PART_TEXT = "这是一段足够长的分P正文，用来验证字幕与语音转写两条路径合并后无法区分来源。" * 3


@pytest.fixture
def factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


# ---------------------------------------------------------------------------
# 1. 创建路径不得声称来源
# ---------------------------------------------------------------------------

def test_content_part_column_default_is_unset_not_whisper_asr():
    """ORM 侧默认值必须是"未产生"，不是任何具体来源。"""
    assert ContentPart.__table__.c.transcript_source.default.arg == TRANSCRIPT_SOURCE_UNSET
    assert TRANSCRIPT_SOURCE_UNSET != "whisper_asr"


def test_new_content_part_does_not_claim_a_source(factory):
    with factory() as db:
        item = ContentItem(platform="bilibili", remote_item_id="BV1", title="视频", duration=100)
        db.add(item)
        db.flush()
        db.add(ContentPart(content_item_id=item.id, remote_part_id="100", part_index=1, part_title="P1"))
        db.commit()

        part = db.scalar(select(ContentPart))
        assert part.transcript_source == TRANSCRIPT_SOURCE_UNSET
        assert part.transcript_source != "whisper_asr"


def test_favorites_sync_does_not_prewrite_transcript_source(factory, monkeypatch):
    """真实同步路径：新建分 P（含 default 占位行）都不得预写来源。"""
    fake_chroma = Mock(delete_by_video=Mock())
    monkeypatch.setattr(favorites_service_module, "get_chroma_service", lambda: fake_chroma)

    collection = FavoriteScrapedCollection(platform_collection_id="c1", title="合集", video_count=2)
    placeholder = FavoriteScrapedVideo(
        platform_item_id="BV_placeholder", url="", title="未富化", author="", duration=100,
        collection_ids={"c1"}, parts=[], freshly_enriched=False,
    )
    with_parts = FavoriteScrapedVideo(
        platform_item_id="BV_parts", url="", title="多P", author="", duration=200,
        collection_ids={"c1"}, freshly_enriched=True,
        parts=[
            {"remote_part_id": "100", "part_index": 1, "part_title": "P1", "duration": 100},
            {"remote_part_id": "101", "part_index": 2, "part_title": "P2", "duration": 100},
        ],
    )

    with factory() as db:
        favorites_service.save_snapshot_to_db(
            db,
            FavoriteScrapeSnapshot(
                collections=[collection], videos=[placeholder, with_parts], platform="bilibili"
            ),
        )
        parts = db.scalars(select(ContentPart).order_by(ContentPart.remote_part_id)).all()
        assert [p.remote_part_id for p in parts] == ["100", "101", "default"]
        assert {p.transcript_source for p in parts} == {TRANSCRIPT_SOURCE_UNSET}
        assert "whisper_asr" not in {p.transcript_source for p in parts}


# ---------------------------------------------------------------------------
# 2. 自动补建 ContentItem：平台事实来自注册表，不伪造抖音
# ---------------------------------------------------------------------------

def test_hook_refuses_to_fabricate_a_douyin_record_without_platform(factory):
    """没有平台提示时，钩子既不知道平台也不允许猜——必须拒绝，而不是写抖音。"""
    with factory() as db:
        db.add(IngestionItem(platform_item_id="answer-123", title="知乎回答"))
        with pytest.raises(ValueError, match="拒绝伪造"):
            db.commit()


def test_hook_creates_item_from_registry_facts_when_platform_is_given(factory):
    with factory() as db:
        db.add(IngestionItem(platform_item_id="BV1xx411c7mD", title="B站视频", platform="bilibili"))
        db.commit()

        item = db.scalar(select(ContentItem))
        assert item.platform == "bilibili"
        assert item.canonical_url == "https://www.bilibili.com/video/BV1xx411c7mD"
        assert "douyin.com" not in item.canonical_url
        # 形态判据只有一处：duration=0 ⇒ 图文（历史实现在这里写死 "video"）。
        assert item.content_kind == content_kind.content_kind_for("bilibili", item.duration)


def test_hook_links_the_item_of_the_requested_platform_only(factory):
    """同一 remote_item_id 可以跨平台重复，钩子必须按平台限定查找。"""
    with factory() as db:
        douyin = ContentItem(platform="douyin", remote_item_id="12345", title="抖音")
        bilibili = ContentItem(platform="bilibili", remote_item_id="12345", title="B站")
        db.add_all([douyin, bilibili])
        db.commit()
        douyin_id, bilibili_id = douyin.id, bilibili.id

        db.add(IngestionItem(platform_item_id="12345", title="B站", platform="bilibili"))
        db.commit()

        ingestion = db.scalar(select(IngestionItem))
        assert ingestion.content_item_id == bilibili_id
        assert ingestion.content_item_id != douyin_id


def test_hook_rejects_ambiguous_legacy_remote_id_without_platform(factory):
    """Legacy auto-linking must not choose an arbitrary provider row."""
    with factory() as db:
        db.add_all([
            ContentItem(platform="douyin", remote_item_id="same-id", title="抖音"),
            ContentItem(platform="bilibili", remote_item_id="same-id", title="B站"),
        ])
        db.commit()
        db.add(IngestionItem(platform_item_id="same-id", title="未知来源"))
        with pytest.raises(ValueError, match="多个平台存在"):
            db.commit()


# ---------------------------------------------------------------------------
# 3. 正文产生处写入真实（或诚实的"未知"）来源
# ---------------------------------------------------------------------------

@pytest.fixture
def bilibili_pipeline(tmp_path, monkeypatch):
    """只含一个 B 站多 P 条目的最小流水线；上游全部替身，不触网。"""
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'attribution.db').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(session_module, "session_factory", factory)
    monkeypatch.setattr(knowledge_module, "worker", Worker())
    chroma = SimpleNamespace(upsert_video_chunks=Mock())
    monkeypatch.setattr(knowledge_module, "get_chroma_service", lambda: chroma)
    monkeypatch.setattr(media_module, "clean_audio_cache", Mock())
    embedding = Mock(side_effect=lambda chunks: [[0.1, 0.2] for _ in chunks])
    monkeypatch.setattr(knowledge_module.embedding_client, "embed_texts", embedding)

    fetch_transcript = AsyncMock(return_value=PART_TEXT)
    monkeypatch.setattr(bilibili_content_fetcher, "fetch_transcript", fetch_transcript)
    monkeypatch.setattr(bilibili_client, "aclose", AsyncMock())

    with factory() as db:
        item = ContentItem(
            platform="bilibili", remote_item_id="BV1", title="测试视频", duration=100,
            canonical_url="https://www.bilibili.com/video/BV1",
        )
        db.add(item)
        db.flush()
        db.add(ContentPart(
            content_item_id=item.id, remote_part_id="100", part_index=1, part_title="P1", duration=100,
        ))
        db.add(IngestionItem(content_item_id=item.id, status="pending"))
        db.commit()
        item_id = item.id

    yield SimpleNamespace(
        service=knowledge_module.KnowledgeService(), factory=factory, item_id=item_id,
        fetch_transcript=fetch_transcript, chroma=chroma, engine=engine,
    )
    engine.dispose()


def test_bilibili_part_records_unknown_source_not_a_guess(bilibili_pipeline):
    """抓取器不暴露来源时，只能写 UNKNOWN；绝不能写 "whisper_asr"。"""
    bilibili_pipeline.service._run_sync([bilibili_pipeline.item_id])

    with bilibili_pipeline.factory() as db:
        part = db.scalar(select(ContentPart))
        ingestion = db.scalar(select(IngestionItem))

    bilibili_pipeline.fetch_transcript.assert_awaited()
    assert part.transcript_source == TRANSCRIPT_SOURCE_UNKNOWN
    assert part.transcript_source != "whisper_asr"
    # 正文确实入库了：这不是"为了好看而清空字段"。
    assert ingestion.status == "done"
    assert ingestion.has_substantive_content is True
    assert bilibili_pipeline.chroma.upsert_video_chunks.call_count == 1
