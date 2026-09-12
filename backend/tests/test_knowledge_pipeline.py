"""入库恢复、并发与失败传播；仅使用临时 SQLite 和替身上游服务。"""
from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, event, select, update
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db import session as session_module
from app.models.entities import FavoriteCollection, FavoriteVideo, VideoCache
from app.services import knowledge_service as knowledge_module
from app.services import media_service as media_module
from app.services.vision_service import vision_service
from app.services.worker import Worker


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'pipeline.db').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(engine)
    database_lease = threading.local()

    @event.listens_for(engine, "checkout")
    def checked_out(connection, record, proxy):
        database_lease.count = getattr(database_lease, "count", 0) + 1

    @event.listens_for(engine, "checkin")
    def checked_in(connection, record):
        database_lease.count -= 1

    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(session_module, "session_factory", factory)
    progress = Worker()
    monkeypatch.setattr(knowledge_module, "worker", progress)
    chroma = SimpleNamespace(upsert_video_chunks=Mock())
    monkeypatch.setattr(knowledge_module, "get_chroma_service", lambda: chroma)
    monkeypatch.setattr(media_module, "clean_audio_cache", Mock())

    active_leases = set()

    @contextmanager
    def lease(item_id):
        active_leases.add(item_id)
        try:
            yield
        finally:
            active_leases.remove(item_id)

    monkeypatch.setattr(media_module, "audio_cache_lease", lease, raising=False)

    def download(url, item_id):
        assert item_id in active_leases
        assert getattr(database_lease, "count", 0) == 0
        path = tmp_path / f"{item_id}.mp3"
        path.write_bytes(b"fixture audio")
        return path

    downloader = Mock(side_effect=download)
    monkeypatch.setattr(knowledge_module, "download_audio", downloader)

    def transcribe(path, **_):
        assert path.stem in active_leases
        assert getattr(database_lease, "count", 0) == 0
        return "这是音频中提取出的有效正文，用于验证断点续传和事务释放。"

    asr = Mock(side_effect=transcribe)
    monkeypatch.setattr(knowledge_module.asr_service, "transcribe_to_text", asr)

    def embed(chunks):
        assert getattr(database_lease, "count", 0) == 0
        return [[0.1, 0.2] for _ in chunks]

    embedding = Mock(side_effect=embed)
    monkeypatch.setattr(knowledge_module.embedding_client, "embed_texts", embedding)
    fetch_images = Mock(return_value=[])
    monkeypatch.setattr(vision_service, "fetch_note_image_urls", fetch_images)
    monkeypatch.setattr(vision_service, "extract_text_from_images", Mock(return_value=""))

    def add(item_id="1001", status="pending", duration=20, transcript=""):
        with factory() as db:
            collection = db.scalar(select(FavoriteCollection))
            if collection is None:
                collection = FavoriteCollection(platform_collection_id="collection", title="收藏")
                db.add(collection)
                db.flush()
            db.add(FavoriteVideo(
                collection_id=collection.id,
                platform_item_id=item_id,
                title="测试作品",
                duration=duration,
            ))
            db.add(VideoCache(
                platform_item_id=item_id,
                title="测试作品",
                status=status,
                transcript_text=transcript,
            ))
            db.commit()

    def cached(item_id="1001"):
        with factory() as db:
            return db.scalar(select(VideoCache).where(VideoCache.platform_item_id == item_id))

    yield SimpleNamespace(
        service=knowledge_module.KnowledgeService(), factory=factory,
        engine=engine, progress=progress, chroma=chroma, add=add, cached=cached,
        download=downloader, asr=asr, embedding=embedding,
        fetch_images=fetch_images, tmp_path=tmp_path,
    )
    engine.dispose()


def test_empty_selected_request_does_not_submit_all_items(pipeline):
    pipeline.add()
    with pipeline.factory() as db:
        result = pipeline.service.start_sync(db, scope="selected", selected_ids=[])
    assert result["pending_count"] == 0
    assert not pipeline.progress.has_active_tasks()
    assert pipeline.cached().status == "pending"


def test_download_error_keeps_real_reason_without_cover_fallback(pipeline):
    pipeline.add()
    pipeline.download.side_effect = RuntimeError("Fresh cookies needed: fixture")
    with pytest.raises(RuntimeError, match="1/1"):
        pipeline.service._run_sync(["1001"])
    assert pipeline.cached().status == "failed"
    assert "Fresh cookies needed" in pipeline.cached().error_message
    pipeline.fetch_images.assert_not_called()
    pipeline.asr.assert_not_called()
    pipeline.chroma.upsert_video_chunks.assert_not_called()


def test_selected_retry_only_resets_the_requested_failure(pipeline):
    pipeline.add("1001", status="failed", transcript="这是已经保存的音频正文检查点，用于重试。")
    pipeline.add("1002", status="failed")
    pipeline.add("1003", status="transcribing")
    with pipeline.factory() as db:
        result = pipeline.service.start_sync(db, scope="selected", selected_ids=["1001", "1003"])
    assert result["pending_count"] == 1
    assert pipeline.cached("1001").status == "pending"
    assert pipeline.cached("1001").transcript_text
    assert pipeline.cached("1002").status == "failed"
    assert pipeline.cached("1003").status == "transcribing"


def test_embedding_failure_preserves_asr_checkpoint_for_retry(pipeline):
    pipeline.add()
    pipeline.embedding.side_effect = RuntimeError("embedding unavailable")
    with pytest.raises(RuntimeError, match="1/1"):
        pipeline.service._run_sync(["1001"])
    failed = pipeline.cached()
    assert failed.status == "failed"
    assert failed.transcript_text.startswith("这是音频")
    assert not (pipeline.tmp_path / "1001.mp3").exists()
    with pipeline.factory() as db:
        db.execute(update(VideoCache).values(status="pending"))
        db.commit()
    pipeline.embedding.side_effect = lambda chunks: [[0.3, 0.4] for _ in chunks]
    pipeline.service._run_sync(["1001"])
    result = pipeline.cached()
    assert result.status == "done"
    assert result.processed_at is not None
    assert result.error_message == ""
    assert pipeline.download.call_count == pipeline.asr.call_count == 1


def test_repeated_queue_entries_skip_done_and_failed_items(pipeline):
    pipeline.add("1001", status="done", transcript="必须保留的既有正文")
    pipeline.add("1002", status="failed")
    pipeline.service._run_sync(["1001", "1001", "1002"])
    assert pipeline.cached("1001").transcript_text == "必须保留的既有正文"
    assert pipeline.cached("1002").status == "failed"
    pipeline.download.assert_not_called()
    pipeline.chroma.upsert_video_chunks.assert_not_called()


def test_concurrent_claim_runs_one_pipeline_per_item(pipeline):
    pipeline.add()
    # 三个独立批次争抢同一条 pending 记录，仅一个可以下载和写向量。
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(pipeline.service._run_sync, ["1001"]) for _ in range(3)]
        for future in futures:
            future.result(timeout=10)
    assert pipeline.cached().status == "done"
    assert pipeline.download.call_count == 1
    assert pipeline.chroma.upsert_video_chunks.call_count == 1


def test_invalid_embedding_does_not_write_chroma(pipeline):
    pipeline.add(transcript="这是已保存的正文检查点，应当直接用于向量化。")
    pipeline.embedding.side_effect = lambda chunks: []
    with pytest.raises(RuntimeError, match="1/1"):
        pipeline.service._run_sync(["1001"])
    assert pipeline.cached().status == "failed"
    assert "向量数量" in pipeline.cached().error_message
    pipeline.chroma.upsert_video_chunks.assert_not_called()


def test_mixed_batch_preserves_success_and_reports_failure(pipeline):
    pipeline.add("1001", transcript="这是第一个作品已经保存的完整音频正文。")
    pipeline.add("1002")
    pipeline.download.side_effect = RuntimeError("upstream timed out")
    with pytest.raises(RuntimeError, match="1/2"):
        pipeline.service._run_sync(["1001", "1002"])
    assert pipeline.cached("1001").status == "done"
    assert pipeline.cached("1002").status == "failed"


def test_reset_does_not_overwrite_active_batch(pipeline, monkeypatch):
    from app.api.routes import knowledge as route

    monkeypatch.setattr(route, "worker", pipeline.progress)
    pipeline.add(status="transcribing")
    pipeline.progress.submit("active", lambda: None)
    with pipeline.factory() as db:
        result = asyncio.run(route.reset_failed_videos(db))
    assert result["success"] is False
    assert pipeline.cached().status == "transcribing"


def test_idle_reset_keeps_transcript_checkpoint(pipeline, monkeypatch):
    from app.api.routes import knowledge as route

    monkeypatch.setattr(route, "worker", pipeline.progress)
    pipeline.add(status="failed", transcript="这是一份重试时必须保留的音频转写检查点。")
    with pipeline.factory() as db:
        result = asyncio.run(route.reset_failed_videos(db))
    assert result == {"success": True, "reset_count": 1}
    assert pipeline.cached().status == "pending"
    assert pipeline.cached().transcript_text


def test_worker_progress_is_snapshot_and_initialized_before_publish():
    worker = Worker()
    worker.submit("task", lambda: None, progress_total=2, progress_message="init")
    snapshot = worker.get_progress("task")
    assert snapshot["total"] == 2
    assert snapshot["message"] == "init"
    snapshot["status"] = "done"
    assert worker.get_progress("task")["status"] == "queued"
    assert worker.has_active_tasks()


def test_worker_cancel_marks_task_cancelled_not_done():
    worker = Worker()

    def body(*, task_id):
        # 模拟执行体在收费节点检查取消并提前返回
        for _ in range(5):
            if worker.is_cancelled(task_id):
                return
            time.sleep(0.02)

    worker.submit("t", body, progress_total=3)
    worker.start()
    try:
        assert worker.cancel("t") is True
        worker._queue.join()
        assert worker.get_progress("t")["status"] == "cancelled"
        # 已结束的任务不能再次取消
        assert worker.cancel("t") is False
    finally:
        worker.stop()
        worker._thread.join(timeout=2)


def test_run_sync_cancel_resets_and_does_not_raise(pipeline):
    pipeline.add("1001", status="pending")
    pipeline.progress._tasks["cx"] = {"status": "running", "cancelled": True,
                                     "progress": 0, "total": 1, "message": ""}
    # 取消标记已置位：_run_sync 应直接收尾、不抛异常、不触碰收费接口
    pipeline.service._run_sync(["1001"], task_id="cx")
    pipeline.asr.assert_not_called()
    pipeline.download.assert_not_called()
    assert pipeline.cached("1001").status == "pending"


def test_worker_records_pipeline_exception_as_failed():
    worker = Worker()

    def fail(*, task_id):
        worker.update_progress(task_id, 2, 2)
        raise RuntimeError("入库处理结束：1/2 个内容失败")

    worker.submit("task", fail, progress_total=2)
    worker.start()
    try:
        worker._queue.join()
        assert worker.get_progress("task")["status"] == "failed"
        worker.update_progress("task", 0, 0, "late initialization")
        assert worker.get_progress("task")["progress"] == 2
        assert "1/2" in worker.get_progress("task")["message"]
    finally:
        worker.stop()
        worker._thread.join(timeout=2)
