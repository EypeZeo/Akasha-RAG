"""入库门禁：阈值收敛（10/20/50）、``has_substantive_content`` 的唯一 owner、
以及平台能力预检必须发生在付费阶段之前。

这个文件覆盖三件互相关联的事，因为它们都是"入库前的那道闸门"：

1. 阈值不再散落成字面量（``app/services/substantive.py`` 是唯一来源），
   且**数值一字未改**——放宽任何一个值都会让"仅标题"内容污染向量库；
2. ``ingestion_items.has_substantive_content`` 过去只被写成 ``False``，
   现在由 ``KnowledgeService.save_state`` 按同一条规则写 True/False 两个状态；
3. 平台不受支持时必须在音频下载 / 付费 ASR / Embedding **之前**失败，
   而不是在写向量库那一步才抛裸 ``ValueError``。

本文件里的 pipeline 夹具只使用临时 SQLite 与替身上游服务，不触网、不花钱。
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import session as session_module
from app.db.base import Base
from app.models.entities import ContentItem, FavoriteCollection, IngestionItem
from app.services import knowledge_service as knowledge_module
from app.services import media_service as media_module
from app.services import substantive
from app.services.bilibili.content_fetcher import SUBSTANTIVE_TEXT_MIN_CHARS
from app.services.worker import Worker

VALID_ASR_TEXT = "这是音频中提取出的有效正文，长度足够通过入库门禁。"


# ---------------------------------------------------------------------------
# 1. 阈值：数值不变，来源唯一
# ---------------------------------------------------------------------------

def test_threshold_values_are_unchanged():
    """10/20/50 三个值必须与历史实现完全一致（去重，不是放宽）。"""
    assert substantive.MIN_INDEXABLE_CHARS == 10
    assert substantive.TITLE_FALLBACK_MIN_CHARS == 20
    assert substantive.BILIBILI_SUBSTANTIVE_TEXT_MIN_CHARS == 50


def test_bilibili_runtime_copy_does_not_drift():
    """B 站抓取器里的运行时副本必须与 substantive 的具名常量同值。

    ``bilibili/content_fetcher.py`` 不在本次改动的写入范围内，所以那边保留了
    自己的常量；本测试让"两处各写一遍"这个风险变成 CI 可见的失败，而不是
    某天悄悄漂移成两个不同的门禁。
    """
    assert SUBSTANTIVE_TEXT_MIN_CHARS == substantive.BILIBILI_SUBSTANTIVE_TEXT_MIN_CHARS


@pytest.mark.parametrize(
    "text, expected",
    [
        (None, False),
        ("", False),
        ("   \n\t ", False),
        ("短" * 9, False),
        ("短" * 10, True),
        ("短" * 11, True),
        (f"  {'短' * 10}  ", True),  # 去空白后再判定
    ],
)
def test_has_indexable_text_boundaries(text, expected):
    assert substantive.has_indexable_text(text) is expected


@pytest.mark.parametrize(
    "title, expected",
    [(None, False), ("", False), ("标" * 19, False), ("标" * 20, True), ("标" * 21, True)],
)
def test_title_fallback_boundary(title, expected):
    assert substantive.title_fallback_is_usable(title) is expected


def test_knowledge_service_no_longer_holds_its_own_threshold_literals():
    """流水线里不允许再出现那 5 处散落的阈值表达式（漂移守卫）。

    这是文本断言，故意写得具体：它锁定的正是本次被替换掉的那几个表达式。
    """
    source = Path(knowledge_module.__file__).read_text(encoding="utf-8")
    for legacy_literal in (
        "len(transcript_text.strip()) >= 10",
        "len(transcript_text.strip()) < 10",
        "len(extracted_text.strip()) >= 10",
        "len(clean_title) >= 20",
        "not transcript_text or len(transcript_text.strip()) < 10",
    ):
        assert legacy_literal not in source, f"阈值字面量复活: {legacy_literal}"
    assert source.count("substantive.has_indexable_text(") >= 4


# ---------------------------------------------------------------------------
# pipeline 夹具：临时 SQLite + 替身上游（不触网、不花钱）
# ---------------------------------------------------------------------------

@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'gates.db').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(session_module, "session_factory", factory)
    monkeypatch.setattr(knowledge_module, "worker", Worker())

    chroma = SimpleNamespace(upsert_video_chunks=Mock())
    monkeypatch.setattr(knowledge_module, "get_chroma_service", lambda: chroma)
    monkeypatch.setattr(media_module, "clean_audio_cache", Mock())

    @contextmanager
    def lease(item_id):
        yield

    monkeypatch.setattr(media_module, "audio_cache_lease", lease, raising=False)

    def download(url, item_id):
        path = tmp_path / f"{item_id}.mp3"
        path.write_bytes(b"fixture audio")
        return path

    downloader = Mock(side_effect=download)
    monkeypatch.setattr(knowledge_module, "download_audio", downloader)
    asr = Mock(side_effect=lambda path, **_: VALID_ASR_TEXT)
    monkeypatch.setattr(knowledge_module.asr_service, "transcribe_to_text", asr)
    embedding = Mock(side_effect=lambda chunks: [[0.1, 0.2] for _ in chunks])
    monkeypatch.setattr(knowledge_module.embedding_client, "embed_texts", embedding)

    def add(platform="douyin", remote_id="1001", duration=20, status="pending",
            transcript="", substantive_flag=False):
        with factory() as db:
            collection = FavoriteCollection(
                platform=platform, platform_collection_id="c1", title="收藏"
            )
            db.add(collection)
            db.flush()
            item = ContentItem(
                platform=platform, remote_item_id=remote_id, title="测试作品",
                duration=duration, collection_id=collection.id,
            )
            db.add(item)
            db.flush()
            db.add(IngestionItem(
                content_item_id=item.id, status=status, transcript_text=transcript,
                has_substantive_content=substantive_flag,
            ))
            db.commit()
            return item.id

    def ingestion(content_item_id):
        with factory() as db:
            return db.scalar(
                select(IngestionItem).where(IngestionItem.content_item_id == content_item_id)
            )

    yield SimpleNamespace(
        service=knowledge_module.KnowledgeService(), factory=factory, add=add,
        ingestion=ingestion, download=downloader, asr=asr, embedding=embedding,
        chroma=chroma, engine=engine,
    )
    engine.dispose()


# ---------------------------------------------------------------------------
# 2. has_substantive_content：唯一 owner，两个状态都写
# ---------------------------------------------------------------------------

def test_successful_ingestion_sets_substantive_flag_true(pipeline):
    item_id = pipeline.add()
    pipeline.service._run_sync([item_id])

    row = pipeline.ingestion(item_id)
    assert row.status == "done"
    assert row.transcript_text.strip()
    assert row.has_substantive_content is True, (
        "写入了正文却没有把 has_substantive_content 写成 True —— 死列回来了"
    )


def test_rejected_short_asr_text_sets_substantive_flag_false(pipeline):
    """正文不足 10 字符时必须拒绝入库，且标志为 False（同一 owner 的另一个状态）。"""
    pipeline.asr.side_effect = lambda path, **_: "只有九个字啊啊啊"
    item_id = pipeline.add()

    with pytest.raises(RuntimeError, match="1/1"):
        pipeline.service._run_sync([item_id])

    row = pipeline.ingestion(item_id)
    assert row.status == "failed"
    assert "拒绝仅标题入库" in row.error_message
    assert row.has_substantive_content is False
    pipeline.chroma.upsert_video_chunks.assert_not_called()


def test_embedding_failure_keeps_flag_true_with_checkpoint(pipeline):
    """Embedding 失败时正文检查点已提交：标志为 True、状态为 failed 是可续传状态。"""
    pipeline.embedding.side_effect = RuntimeError("embedding unavailable")
    item_id = pipeline.add()

    with pytest.raises(RuntimeError, match="1/1"):
        pipeline.service._run_sync([item_id])

    row = pipeline.ingestion(item_id)
    assert row.status == "failed"
    assert row.transcript_text.strip()
    assert row.has_substantive_content is True


def test_resumed_checkpoint_row_with_stale_false_flag_is_corrected(pipeline):
    """续传行不会重写正文，但进入完成态时必须重算标志。

    场景来自遗留迁移：>= 50 口径的回填会给 10..49 字符的存量正文留下 False，
    而续传路径（已有 checkpoint）直接向量化、不再写正文——不重算就会造出
    "正文已进向量库、标志却是 False" 的又一个谎言。
    """
    stale_text = "这是遗留下来的十二字正文样本内容"  # 16 字符：>= 10 且 < 50
    assert 10 <= len(stale_text) < 50
    item_id = pipeline.add(transcript=stale_text, substantive_flag=False)

    pipeline.service._run_sync([item_id])

    row = pipeline.ingestion(item_id)
    assert row.status == "done"
    assert row.has_substantive_content is True
    # 续传语义没有被改变：有检查点就不该再下载/转写（更不该再花钱）。
    pipeline.download.assert_not_called()
    pipeline.asr.assert_not_called()


# ---------------------------------------------------------------------------
# 3. 平台能力预检：钱不能花在不支持的平台上
# ---------------------------------------------------------------------------

def test_unsupported_platform_rejected_before_any_paid_stage(pipeline):
    """未登记平台必须在下载/ASR/Embedding 之前失败，且原因可读、可机器识别。"""
    item_id = pipeline.add(platform="xiaohongshu", remote_id="answer-1")

    with pytest.raises(RuntimeError, match="1/1"):
        pipeline.service._run_sync([item_id])

    pipeline.download.assert_not_called()
    pipeline.asr.assert_not_called()
    pipeline.embedding.assert_not_called()
    pipeline.chroma.upsert_video_chunks.assert_not_called()

    row = pipeline.ingestion(item_id)
    assert row.status == "failed"
    assert row.error_code == knowledge_module.ERROR_CODE_UNSUPPORTED_PLATFORM
    assert "不支持的平台" in row.error_message
    assert "xiaohongshu" in row.error_message
    # 拒绝原因里要给出当前真正支持的平台，用户才知道下一步做什么。
    assert "douyin" in row.error_message and "bilibili" in row.error_message


def test_supported_platform_is_not_blocked_by_the_precheck(pipeline):
    """预检不能误伤已登记平台（否则就是把拒绝前移变成了全部拒绝）。"""
    item_id = pipeline.add(platform="douyin")
    pipeline.service._run_sync([item_id])
    assert pipeline.ingestion(item_id).status == "done"
