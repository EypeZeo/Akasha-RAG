"""
NET-06 回归测试：取消必须走正常控制流，不能被误判成 failed

`asr_service.transcribe_to_text` 取消时抛出的是一个裸 `RuntimeError`，和真实
失败用的是同一个异常类型；`process_one_video()` 原来的宽泛 `except Exception`
会把它当成入库失败写成 `status="failed"`，而不是复用现有的 `pending` 回退
语义。修复后：
- `BilibiliContentFetcher.fetch_transcript` 在字幕探测/字幕下载/DASH 下载/
  转码这几个步骤前检查取消，命中就抛出专用的 `TranscriptionCancelled`；
  字幕阶段的宽泛 `except Exception` 不会把这个信号误吞成"降级走音频"；
- ASR 抛出的裸 `RuntimeError` 由调用方（`fetch_transcript` 与
  `knowledge_service.py` 里抖音音频转写调用点）用 `cancel_check()` 复核后
  转换成 `TranscriptionCancelled`，不靠异常消息文本匹配；
- `knowledge_service.py::_fetch_all_parts` 在分 P 之间检查取消，命中就
  停止并返回"已取消"标记，`process_one_video()` 据此调用现有的
  `bail_if_cancelled(...)` 回退到 `pending`，不落入通用失败处理；
- 无论取消发生在哪个环节，`bilibili_client.aclose()`、临时音频文件清理都
  必须照常执行。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import session as session_module
from app.db.base import Base
from app.models.entities import ContentItem, ContentPart, IngestionItem
from app.services import knowledge_service as knowledge_module
from app.services.bilibili.content_fetcher import (
    BilibiliContentFetcher,
    TranscriptionCancelled,
)
from app.services.worker import Worker


async def _async(value):
    return value


async def _download(path):
    path.write_bytes(b"m4a fixture")
    return True


# ---------------------------------------------------------------------------
# 单元级测试：直接针对 BilibiliContentFetcher.fetch_transcript
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancellation_after_subtitle_probe_skips_download_and_dash(tmp_path):
    """字幕探测完成后才触发取消：不应该再去下载字幕，也不应该降级走 DASH。"""
    fetcher = BilibiliContentFetcher()
    fetcher._cache_dir = tmp_path
    cancel_state = {"v": False}

    async def get_player_info(*_):
        # 探测阶段本身还没有取消——取消发生在"探测完成、决定要不要下载
        # 字幕"这一步之间。
        cancel_state["v"] = True
        return {"subtitle": {"subtitles": [{"lan": "zh-CN", "subtitle_url": "http://x"}]}}

    async def download_subtitle_should_not_run(*_):
        raise AssertionError("取消之后不应该再去下载字幕")

    async def get_audio_url_should_not_run(*_):
        raise AssertionError("取消之后不应该降级走 DASH 音频下载")

    fetcher._client = SimpleNamespace(
        get_player_info=get_player_info,
        download_subtitle=download_subtitle_should_not_run,
        get_audio_url=get_audio_url_should_not_run,
    )

    with pytest.raises(TranscriptionCancelled):
        await fetcher.fetch_transcript(
            "BV1xx", 1001, title="测试视频", cancel_check=lambda: cancel_state["v"],
        )


@pytest.mark.asyncio
async def test_asr_runtime_error_during_cancellation_is_identified_as_cancelled(tmp_path, monkeypatch):
    """ASR 抛出的裸 RuntimeError，在取消确实发生时必须被识别为取消，不是失败。"""
    fetcher = BilibiliContentFetcher()
    fetcher._cache_dir = tmp_path
    fetcher._client = SimpleNamespace(
        get_player_info=lambda *_: _async({}),  # 无字幕，走 DASH
        get_audio_url=lambda *_: _async("https://upos-sz-mirrorcos.bilivideo.com/audio.m4a"),
        download_audio_to_file=lambda _url, path: _download(path),
    )
    monkeypatch.setattr(
        "app.services.bilibili.content_fetcher.transcode_audio_to_mp3",
        lambda source, destination: destination.write_bytes(b"mp3 fixture"),
    )

    cancel_state = {"v": False}

    def fake_transcribe(path, cancel_check=None):
        # 模拟 asr_service 在 cancel_check() 确认取消后抛出的裸 RuntimeError。
        cancel_state["v"] = True
        raise RuntimeError("ASR 转写已取消，工作进程已终止")

    monkeypatch.setattr(
        "app.services.bilibili.content_fetcher.asr_service.transcribe_to_text",
        fake_transcribe,
    )

    with pytest.raises(TranscriptionCancelled):
        await fetcher.fetch_transcript(
            "BV1xx", 1001, title="测试视频", cancel_check=lambda: cancel_state["v"],
        )


@pytest.mark.asyncio
async def test_asr_runtime_error_without_cancellation_stays_a_real_failure(tmp_path, monkeypatch):
    """没有取消信号时，ASR 的 RuntimeError 必须原样冒泡，不能被误吞成取消。"""
    fetcher = BilibiliContentFetcher()
    fetcher._cache_dir = tmp_path
    fetcher._client = SimpleNamespace(
        get_player_info=lambda *_: _async({}),
        get_audio_url=lambda *_: _async("https://upos-sz-mirrorcos.bilivideo.com/audio.m4a"),
        download_audio_to_file=lambda _url, path: _download(path),
    )
    monkeypatch.setattr(
        "app.services.bilibili.content_fetcher.transcode_audio_to_mp3",
        lambda source, destination: destination.write_bytes(b"mp3 fixture"),
    )

    def fake_transcribe(path, cancel_check=None):
        raise RuntimeError("ASR 转写失败：模型服务不可用")

    monkeypatch.setattr(
        "app.services.bilibili.content_fetcher.asr_service.transcribe_to_text",
        fake_transcribe,
    )

    with pytest.raises(RuntimeError, match="模型服务不可用"):
        await fetcher.fetch_transcript(
            "BV1xx", 1001, title="测试视频", cancel_check=lambda: False,
        )


@pytest.mark.asyncio
async def test_cancellation_during_asr_still_cleans_up_temp_audio_files(tmp_path, monkeypatch):
    """无论取消发生在哪个环节，临时音频文件都必须被清理，不留下垃圾文件。"""
    fetcher = BilibiliContentFetcher()
    fetcher._cache_dir = tmp_path
    fetcher._client = SimpleNamespace(
        get_player_info=lambda *_: _async({}),
        get_audio_url=lambda *_: _async("https://upos-sz-mirrorcos.bilivideo.com/audio.m4a"),
        download_audio_to_file=lambda _url, path: _download(path),
    )
    monkeypatch.setattr(
        "app.services.bilibili.content_fetcher.transcode_audio_to_mp3",
        lambda source, destination: destination.write_bytes(b"mp3 fixture"),
    )
    cancel_state = {"v": False}

    def fake_transcribe(path, cancel_check=None):
        cancel_state["v"] = True
        raise RuntimeError("ASR 转写已取消，工作进程已终止")

    monkeypatch.setattr(
        "app.services.bilibili.content_fetcher.asr_service.transcribe_to_text",
        fake_transcribe,
    )

    with pytest.raises(TranscriptionCancelled):
        await fetcher.fetch_transcript(
            "BV1xx", 1001, title="测试视频", cancel_check=lambda: cancel_state["v"],
        )

    assert not list(tmp_path.iterdir())


# ---------------------------------------------------------------------------
# 端到端测试：经由 KnowledgeService._run_sync，验证最终落地状态与清理动作
# ---------------------------------------------------------------------------


@pytest.fixture
def bili_pipeline(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'bili_cancel.db').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(session_module, "session_factory", factory)

    progress = Worker()
    monkeypatch.setattr(knowledge_module, "worker", progress)
    chroma = SimpleNamespace(upsert_video_chunks=Mock())
    monkeypatch.setattr(knowledge_module, "get_chroma_service", lambda: chroma)

    aclose_mock = AsyncMock()
    bilibili_client_stub = SimpleNamespace(aclose=aclose_mock)
    monkeypatch.setattr("app.services.bilibili.client.bilibili_client", bilibili_client_stub)

    def add_bilibili(item_id="BV1xx", num_parts=3, status="pending"):
        with factory() as db:
            item = ContentItem(
                platform="bilibili", remote_item_id=item_id, canonical_url=f"https://x/{item_id}",
                title="B站测试视频", author="up", duration=100 * num_parts,
                content_kind="video", part_count=num_parts, is_active=True,
            )
            db.add(item)
            db.flush()
            for i in range(1, num_parts + 1):
                db.add(ContentPart(
                    content_item_id=item.id, remote_part_id=str(i), part_index=i, part_title=f"P{i}",
                    duration=100, transcript_source="whisper_asr", transcript_version="1", time_range="0-100",
                ))
            db.add(IngestionItem(content_item_id=item.id, status=status, transcript_text=""))
            db.commit()
            return item.id

    def cached(content_item_id):
        with factory() as db:
            return db.scalar(select(IngestionItem).where(IngestionItem.content_item_id == content_item_id))

    yield SimpleNamespace(
        service=knowledge_module.KnowledgeService(), factory=factory, progress=progress,
        chroma=chroma, aclose=aclose_mock, add_bilibili=add_bilibili, cached=cached,
    )
    engine.dispose()


def test_multipart_cancellation_stops_fetching_further_parts(bili_pipeline, monkeypatch):
    """三分 P 视频，第二分 P 开始前触发取消：fetch_transcript 只应被调用一次。"""
    item_id = bili_pipeline.add_bilibili(num_parts=3)
    task_id = "cancel-mid-parts"
    bili_pipeline.progress._tasks[task_id] = {
        "status": "running", "cancelled": False, "progress": 0, "total": 1, "message": "",
    }

    call_count = {"n": 0}

    async def fake_fetch_transcript(bvid, cid, title="", part_title="", cancel_check=None):
        call_count["n"] += 1
        # 第一分 P 处理完成后才置位取消，模拟"取消发生在第二分 P 开始前"。
        bili_pipeline.progress._tasks[task_id]["cancelled"] = True
        return f"P{cid} 正文"

    monkeypatch.setattr(
        "app.services.bilibili.content_fetcher.bilibili_content_fetcher.fetch_transcript",
        AsyncMock(side_effect=fake_fetch_transcript),
    )

    bili_pipeline.service._run_sync([item_id], task_id=task_id)

    assert call_count["n"] == 1
    assert bili_pipeline.cached(item_id).status == "pending"
    bili_pipeline.chroma.upsert_video_chunks.assert_not_called()
    bili_pipeline.aclose.assert_awaited()


def test_fetch_transcript_cancellation_resolves_to_pending_not_failed(bili_pipeline, monkeypatch):
    """fetch_transcript 内部抛出 TranscriptionCancelled：最终状态必须是 pending，不是 failed。"""
    item_id = bili_pipeline.add_bilibili(num_parts=1)
    task_id = "cancel-inside-fetch"
    bili_pipeline.progress._tasks[task_id] = {
        "status": "running", "cancelled": False, "progress": 0, "total": 1, "message": "",
    }

    async def fake_fetch_transcript(bvid, cid, title="", part_title="", cancel_check=None):
        # TranscriptionCancelled is only ever raised once cancel_check()
        # (backed by the worker's own cancelled flag) has confirmed True --
        # replicate that invariant here instead of raising it in isolation.
        bili_pipeline.progress._tasks[task_id]["cancelled"] = True
        raise TranscriptionCancelled("模拟字幕/DASH 阶段命中取消")

    monkeypatch.setattr(
        "app.services.bilibili.content_fetcher.bilibili_content_fetcher.fetch_transcript",
        AsyncMock(side_effect=fake_fetch_transcript),
    )

    bili_pipeline.service._run_sync([item_id], task_id=task_id)

    assert bili_pipeline.cached(item_id).status == "pending"
    assert bili_pipeline.cached(item_id).status != "failed"
    bili_pipeline.chroma.upsert_video_chunks.assert_not_called()
    bili_pipeline.aclose.assert_awaited()


def test_douyin_asr_cancellation_resolves_to_pending_not_failed(tmp_path, monkeypatch):
    """抖音音频路径：ASR 取消时的裸 RuntimeError 必须落到 pending，不是 failed。"""
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'douyin_cancel.db').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(session_module, "session_factory", factory)

    progress = Worker()
    monkeypatch.setattr(knowledge_module, "worker", progress)
    chroma = SimpleNamespace(upsert_video_chunks=Mock())
    monkeypatch.setattr(knowledge_module, "get_chroma_service", lambda: chroma)
    monkeypatch.setattr("app.services.media_service.clean_audio_cache", Mock())

    def download(url, item_id):
        path = tmp_path / f"{item_id}.mp3"
        path.write_bytes(b"fixture audio")
        return path

    monkeypatch.setattr(knowledge_module, "download_audio", Mock(side_effect=download))

    task_id = "cancel-during-douyin-asr"
    with factory() as db:
        item = ContentItem(
            platform="douyin", remote_item_id="d1", canonical_url="https://x/d1",
            title="抖音测试视频", duration=30, content_kind="video", part_count=1, is_active=True,
        )
        db.add(item)
        db.flush()
        db.add(IngestionItem(content_item_id=item.id, status="pending", transcript_text=""))
        db.commit()

    progress._tasks[task_id] = {"status": "running", "cancelled": False, "progress": 0, "total": 1, "message": ""}

    def fake_transcribe(path, cancel_check=None):
        progress._tasks[task_id]["cancelled"] = True
        raise RuntimeError("ASR 转写已取消，工作进程已终止")

    monkeypatch.setattr(knowledge_module.asr_service, "transcribe_to_text", Mock(side_effect=fake_transcribe))

    service = knowledge_module.KnowledgeService()
    service._run_sync(["d1"], task_id=task_id)

    with factory() as db:
        cid = db.scalar(select(ContentItem.id).where(ContentItem.remote_item_id == "d1"))
        result = db.scalar(select(IngestionItem).where(IngestionItem.content_item_id == cid))
    assert result.status == "pending"
    chroma.upsert_video_chunks.assert_not_called()
