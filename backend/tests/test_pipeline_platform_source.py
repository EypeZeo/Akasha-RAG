"""围栏测试：钉死入库流水线的**平台来源**行为。

只放"必须真跑一遍 ``process_one_video``"才能证明的断言。纯函数性质与源码守卫
放在 ``test_platform_gate_regressions.py``。

对应缺陷（证据见 docs/audits/WS-VERIFICATION.md 与 WS4-REVERIFY.md）：

M1    兜底 ``else`` 曾手写 ``f"https://www.douyin.com/video/{id}"``。它当时只被抖音
      走到，所以看不出问题；注册第三个平台后它就成了落点，会去下载一个**伪造的
      抖音地址**。源码守卫看不见它（只匹配 ``else f"``，而这是跨行实参）。
M1-b  平台能力一度有两个归属（``capabilities`` 集合 + ``note_extraction`` 布尔量），
      只声明能力集合的平台会被静默改道去下音频做 ASR。
L1    ``item.platform or "douyin"`` 把空串洗成抖音，绕过能力预检，跑完付费链路。
L3-a  平台 id 只在集合查找时被规范化，写进 metadata/chunk_id 的仍是原始拼写，
      于是向量落进正确分区、却因 metadata 不匹配被 search() 全部丢弃。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import session as session_module
from app.db.base import Base
from app.models.entities import ContentItem, ContentPart, IngestionItem, TRANSCRIPT_SOURCE_ARTICLE_TEXT
from app.services import knowledge_service as knowledge_module
from app.services import platform_registry
from app.services.platform_registry import (
    CAPABILITY_AUDIO_ASR,
    CAPABILITY_NOTE_OCR,
    PlatformFacts,
)
from app.services.worker import Worker


@pytest.fixture
def pipe(tmp_path, monkeypatch):
    """一个只含"本测试需要的东西"的最小流水线夹具。

    刻意不复用 tests/test_knowledge_pipeline.py 的夹具：那个夹具建的是遗留
    FavoriteVideo/VideoCache 行，而 ``process_one_video`` 读的是
    ContentItem/IngestionItem/ContentPart。混用会让测试断言错对象。
    """
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'pipe.db').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(session_module, "session_factory", factory)
    monkeypatch.setattr(knowledge_module, "worker", Worker())

    chroma = Mock()
    chroma.upsert_video_chunks = Mock(return_value=[])
    monkeypatch.setattr(knowledge_module, "get_chroma_service", lambda: chroma)

    download = Mock(side_effect=lambda url, item_id: _fake_audio(tmp_path, item_id))
    monkeypatch.setattr(knowledge_module, "download_audio", download)

    asr = Mock(return_value="这是音频中提取出的有效正文，长度足够通过实质性门禁。")
    monkeypatch.setattr(knowledge_module.asr_service, "transcribe_to_text", asr)

    embedding = Mock(side_effect=lambda chunks: [[0.1, 0.2] for _ in chunks])
    monkeypatch.setattr(knowledge_module.embedding_client, "embed_texts", embedding)

    from app.services.vision_service import vision_service

    fetch_images = Mock(return_value=[])
    monkeypatch.setattr(vision_service, "fetch_note_image_urls", fetch_images)
    monkeypatch.setattr(vision_service, "extract_text_from_images", Mock(return_value=""))

    def add(platform="douyin", duration=20, remote_id="r1", title="测试作品", transcript=""):
        with factory() as db:
            item = ContentItem(
                platform=platform, remote_item_id=remote_id, title=title,
                author="作者", duration=duration, canonical_url="", video_url="",
                content_kind="video" if duration > 0 else "note",
            )
            db.add(item)
            db.flush()
            db.add(ContentPart(
                content_item_id=item.id, remote_part_id="default", part_index=1,
                part_title=item.title, duration=duration,
            ))
            db.add(IngestionItem(
                content_item_id=item.id, pipeline_version="t", source_fingerprint="",
                status="pending", transcript_text=transcript, summary="",
            ))
            db.commit()
            return item.id

    yield SimpleNamespace(
        factory=factory, add=add, chroma=chroma, download=download,
        asr=asr, embedding=embedding, fetch_images=fetch_images,
        service=knowledge_module.KnowledgeService(),
    )
    engine.dispose()


def _fake_audio(tmp_path, item_id):
    path = tmp_path / f"{item_id}.mp3"
    path.write_bytes(b"fixture audio")
    return path


def _rows(factory):
    with factory() as db:
        item = db.scalar(select(IngestionItem))
        content = db.scalar(select(ContentItem))
        return content, item


# ---------------------------------------------------------------------------
# M1 — 抖音视频必须走自己的注册表地址，且不被能力门禁误杀
# ---------------------------------------------------------------------------

def test_douyin_video_uses_registry_url_and_completes(pipe):
    """抖音视频走音频 ASR，URL 来自注册表模板。

    这是 M1 修复过程中真实踩到的坑：中途版本用**单值**策略字符串描述平台，把抖音
    标成"仅图文 OCR"，于是抖音视频被判为"未声明音频 ASR 能力"而失败。本测试即该
    回归的探测器 —— 删掉 CAPABILITY_AUDIO_ASR 声明它就会红。
    """
    # 先断言事实本身：抖音必须声明音频 ASR 能力。这条让测试"承重"——
    # 若有人从注册表里删掉该能力（正是当初会误杀抖音视频的那个改动），
    # 仅靠下面的行为断言是抓不住的（行为断言只证明"声明为真时能跑通"）。
    assert platform_registry.platform_supports("douyin", CAPABILITY_AUDIO_ASR) is True, (
        "抖音必须声明 audio_asr 能力，否则它的视频会被门禁误判为不支持（M1 回归）"
    )

    pipe.add(platform="douyin", duration=20)

    pipe.service._run_sync([1])

    _content, item = _rows(pipe.factory)
    assert item.status == "done", item.error_message
    pipe.download.assert_called_once()
    assert pipe.download.call_args.args[0] == "https://www.douyin.com/video/r1"
    assert pipe.asr.call_count == 1
    assert pipe.embedding.call_count == 1
    assert pipe.chroma.upsert_video_chunks.call_count == 1
    # 写入平台必须是规范 id，否则 metadata 过滤会把向量丢掉（L3-a）。
    assert pipe.chroma.upsert_video_chunks.call_args.kwargs["platform"] == "douyin"


def test_unregistered_platform_fails_before_any_paid_call(pipe):
    """未登记平台必须在下载/ASR/Embedding **之前**失败，不能烧完再报错。"""
    pipe.add(platform="xiaohongshu", duration=20)

    # _run_sync 在批次有失败项时汇总抛错；该行本身必须已被标成失败。
    with pytest.raises(RuntimeError, match="1/1"):
        pipe.service._run_sync([1])

    _, item = _rows(pipe.factory)
    assert item.status == "failed"
    assert item.error_code == "unsupported_platform", item.error_message
    pipe.download.assert_not_called()
    pipe.asr.assert_not_called()
    pipe.embedding.assert_not_called()
    pipe.chroma.upsert_video_chunks.assert_not_called()


def test_zhihu_article_checkpoint_uses_embedding_without_audio_or_asr(pipe):
    """知乎已抓取正文直接入库，不应落入视频音频 ASR 分支。"""
    pipe.add(
        platform="zhihu",
        duration=0,
        transcript="这是来自知乎回答的实质正文，长度足够通过入库门禁。",
    )

    pipe.service._run_sync([1])

    content, item = _rows(pipe.factory)
    assert item.status == "done", item.error_message
    pipe.download.assert_not_called()
    pipe.asr.assert_not_called()
    assert pipe.embedding.call_count == 1
    with pipe.factory() as db:
        part = db.query(ContentPart).filter_by(content_item_id=content.id).one()
        assert part.transcript_source == TRANSCRIPT_SOURCE_ARTICLE_TEXT


def test_blank_platform_fails_before_any_paid_call(pipe):
    """L1 的原始反例：``platform=""`` 曾被 ``or "douyin"`` 洗成抖音并跑完整条付费链路。"""
    pipe.add(platform="", duration=20)

    # 批次汇总会抛错；关键是这一行没有产生任何付费调用。
    with pytest.raises(RuntimeError, match="1/1"):
        pipe.service._run_sync([1])

    _, item = _rows(pipe.factory)
    assert item.status == "failed"
    assert item.error_code == "unsupported_platform", item.error_message
    pipe.download.assert_not_called()
    pipe.asr.assert_not_called()
    pipe.embedding.assert_not_called()
    pipe.chroma.upsert_video_chunks.assert_not_called()


# ---------------------------------------------------------------------------
# M1-b — 只声明 OCR 能力的平台不得被静默拉去下音频做 ASR
# ---------------------------------------------------------------------------

def test_note_ocr_capability_is_derived_and_routes_to_ocr(pipe, monkeypatch):
    """声明 ``note_ocr`` 就必须命中图集 OCR 分支，而不是音频 ASR。

    M1-b：能力集合与 ``note_extraction`` 布尔量曾各说各话 —— 平台可以声明
    ``CAPABILITY_NOTE_OCR`` 却没把布尔量置真，于是被判成"非图文"改道去下音频。
    现在布尔量由能力集合派生，两处事实合成一处。
    """
    facts = PlatformFacts(
        platform="probe_ocr", display_name="探针",
        canonical_url_template="https://example.invalid/v/{remote_item_id}",
        image_domains=frozenset(), collection_name="akasha_probe_ocr",
        content_kind_by_duration=True, capabilities=frozenset({CAPABILITY_NOTE_OCR}),
    )
    assert facts.note_extraction is True, "note_extraction 必须由能力集合派生（M1-b 回归）"
    assert CAPABILITY_AUDIO_ASR not in facts.capabilities
    monkeypatch.setitem(platform_registry.PLATFORMS, "probe_ocr", facts)

    # 零时长 = 图文形态。标题必须够长（>=20 字）以通过既有的"拒绝仅标题入库"
    # 门禁的标题兜底分支，否则会因正文太短而失败，测不到路由本身。
    pipe.add(
        platform="probe_ocr", duration=0,
        title="这是一个足够长的图文标题用于通过实质性正文门禁的长度要求",
    )

    pipe.service._run_sync([1])

    _, item = _rows(pipe.factory)
    assert item.status == "done", item.error_message
    # 走了 OCR 分支；没有 audio_asr 能力，所以绝不下音频、绝不调 ASR。
    pipe.fetch_images.assert_called_once()
    pipe.download.assert_not_called()
    pipe.asr.assert_not_called()


def test_platform_with_audio_asr_but_no_subtitle_still_reaches_asr(pipe, monkeypatch):
    """能力的正向对照：显式声明 audio_asr 的平台可以走音频路径。

    与上一条合成一对，防止"门禁写成永远拒绝"这种过度收紧。
    """
    facts = PlatformFacts(
        platform="probe_asr", display_name="探针",
        canonical_url_template="https://example.invalid/a/{remote_item_id}",
        image_domains=frozenset(), collection_name="akasha_probe_asr",
        content_kind_by_duration=True, capabilities=frozenset({CAPABILITY_AUDIO_ASR}),
    )
    monkeypatch.setitem(platform_registry.PLATFORMS, "probe_asr", facts)

    pipe.add(platform="probe_asr", duration=20)

    pipe.service._run_sync([1])

    pipe.download.assert_called_once()
    assert pipe.download.call_args.args[0] == "https://example.invalid/a/r1"
    assert pipe.asr.call_count == 1
