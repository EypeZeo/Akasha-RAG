"""
PERF-03 回归测试：同步时判断 content_item.is_active 不能逐条 COUNT
"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.services.douyin_collector import (
    FavoriteScrapedCollection,
    FavoriteScrapedVideo,
    FavoriteScrapeSnapshot,
)
from app.services.favorites_service import favorites_service


def test_active_link_recompute_is_not_one_query_per_item():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)

    with factory() as db:
        col = FavoriteScrapedCollection(platform_collection_id="c1", title="合集", video_count=60)
        videos = [
            FavoriteScrapedVideo(
                platform_item_id=f"item-{i}", url=f"http://v{i}", title=f"视频{i}",
                author="a", duration=10, collection_ids={"c1"},
            )
            for i in range(60)
        ]
        favorites_service.save_snapshot_to_db(db, FavoriteScrapeSnapshot(collections=[col], videos=videos))

        # Second snapshot drops half the items from the collection, forcing
        # the is_active recompute path (existing_item_map is non-empty) for
        # all 60 previously-known items.
        remaining = videos[:30]
        query_log = []
        event.listen(engine, "before_cursor_execute", lambda *a: query_log.append(a[2]))

        favorites_service.save_snapshot_to_db(db, FavoriteScrapeSnapshot(collections=[col], videos=remaining))

        count_queries = [q for q in query_log if "COUNT" in q.upper()]
        # A handful of fixed summary-stat COUNT queries already existed here
        # (collections/videos/notes totals) -- those don't scale with item
        # count. What must NOT reappear is one COUNT per content item (was
        # 60 before the GROUP BY fix); assert none of the COUNT queries are
        # scoped to a single content_item_id, and the total stays small.
        assert len(count_queries) <= 10
        per_item_count_queries = [q for q in count_queries if "collection_items" in q and "IN (" not in q]
        assert per_item_count_queries == []
