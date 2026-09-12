"""
PERF-03 回归测试：导出视频查询不能是双重 N+1，也不能靠裸字符串跨平台撞键

`get_exportable_videos` 之前对每一行都：① 通过懒加载的 `content_item`
关系隐式发一次查询（`VideoCache.platform_item_id`/`.title` 是路由到
`self.content_item` 的 hybrid_property，查询里没有预加载这个关系）；
② 再单独发一次 `FavoriteVideo.platform_item_id == cache.platform_item_id`
查询——而 `platform_item_id` 只是 `remote_item_id`，在不同平台之间不是
唯一的，两个平台恰好用了同一个 ID 字符串会被错误关联到一起。
"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.entities import ContentItem, IngestionItem
from app.services.batch_export_service import BatchExportService


def _seed_many(db, count: int, platform: str = "douyin"):
    for i in range(count):
        item = ContentItem(platform=platform, remote_item_id=f"{platform}-{i}", title=f"标题{i}")
        db.add(item)
        db.flush()
        db.add(IngestionItem(content_item_id=item.id, status="done", transcript_text="text"))
    db.commit()


def test_export_query_is_o1_regardless_of_row_count(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'export.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine)

    query_log = []
    event.listen(engine, "before_cursor_execute", lambda *a: query_log.append(a[2]))

    with factory() as db:
        _seed_many(db, 50)
        query_log.clear()

        service = BatchExportService()
        results = service.get_exportable_videos(db)

        assert len(results) == 50
        # One query for the videos (+ their eagerly-loaded content_item via a
        # single batched selectinload query) -- not one query per row.
        assert len(query_log) <= 3


def test_export_does_not_collide_across_platforms_sharing_the_same_remote_id(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'export2.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine)

    with factory() as db:
        dy = ContentItem(platform="douyin", remote_item_id="shared-id", title="抖音视频", author="抖音作者")
        bili = ContentItem(platform="bilibili", remote_item_id="shared-id", title="B站视频", author="B站作者")
        db.add_all([dy, bili])
        db.flush()
        db.add(IngestionItem(content_item_id=dy.id, status="done", transcript_text="dy text"))
        db.add(IngestionItem(content_item_id=bili.id, status="done", transcript_text="bili text"))
        db.commit()

        service = BatchExportService()
        results = service.get_exportable_videos(db)

        assert len(results) == 2
        by_platform = {fv.platform: (cache, fv) for cache, fv in results}
        assert by_platform["douyin"][1].author == "抖音作者"
        assert by_platform["bilibili"][1].author == "B站作者"
        assert by_platform["douyin"][0].transcript_text == "dy text"
        assert by_platform["bilibili"][0].transcript_text == "bili text"
