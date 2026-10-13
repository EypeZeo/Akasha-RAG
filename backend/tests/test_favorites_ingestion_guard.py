"""Snapshot publication must not reset rows owned by a running ingestion job."""
import threading

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.entities import IngestionItem
from app.services import worker as worker_module
from app.services.douyin_collector import FavoriteScrapedCollection, FavoriteScrapedVideo, FavoriteScrapeSnapshot
from app.services.favorites_service import FavoritesService
from app.services.worker import Worker


def snapshot(text):
    return FavoriteScrapeSnapshot(
        platform="zhihu",
        collections=[FavoriteScrapedCollection(platform_collection_id="folder", title="收藏", video_count=1)],
        videos=[FavoriteScrapedVideo(
            platform_item_id="article", title="正文", url="https://zhuanlan.zhihu.com/p/123",
            author="作者", duration=0, collection_ids={"folder"}, text_content=text,
        )],
    )


@pytest.mark.parametrize("running", [False, True])
def test_snapshot_cannot_overwrite_queued_or_running_ingestion(tmp_path, monkeypatch, running):
    engine = create_engine(f"sqlite:///{tmp_path / 'snapshot.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    worker = Worker()
    monkeypatch.setattr(worker_module, "worker", worker)
    service = FavoritesService()
    entered, release = threading.Event(), threading.Event()

    def job(*, task_id):
        entered.set()
        assert release.wait(5)

    try:
        with factory() as db:
            service.save_snapshot_to_db(db, snapshot("原来的正文内容，不允许被仍在执行的快照替换。"))
            ingestion = db.scalar(select(IngestionItem))
            ingestion.status = "transcribing"
            db.commit()

        worker.submit("ingesting", job)
        if running:
            worker.start()
            assert entered.wait(5)
        with factory() as db:
            with pytest.raises(RuntimeError, match="入库任务仍在执行或排队"):
                service.save_snapshot_to_db(db, snapshot("新的正文内容，在任务结束前必须拒绝发布。"))
            unchanged = db.scalar(select(IngestionItem))
            assert unchanged.status == "transcribing"
            assert unchanged.transcript_text == "原来的正文内容，不允许被仍在执行的快照替换。"

        release.set()
        if running:
            worker._queue.join()
        else:
            worker.stop()
        with factory() as db:
            result = service.save_snapshot_to_db(db, snapshot("新的正文内容，在任务结束后允许正常发布。"))
            assert result["videos_total"] == 1
            updated = db.scalar(select(IngestionItem))
            assert updated.status == "pending"
            assert updated.transcript_text == "新的正文内容，在任务结束后允许正常发布。"
    finally:
        release.set()
        worker.stop()
        if worker._thread.is_alive():
            worker._thread.join(timeout=5)
        engine.dispose()
