import pytest

from app.services import douyin_collector
from app.services.douyin_collector import DouyinCollector, FavoriteScrapeSnapshot, FavoriteScrapedCollection
from app.services.douyin_favorites_page import DouyinFavoritesPageError, FavoritePages


def test_web_snapshot_normalizes_metadata_and_preserves_all_folder_memberships(monkeypatch):
    collector = DouyinCollector()
    shared_work = {
        "aweme_id": "123456",
        "item_title": "",
        "desc": "A saved video",
        "author": {"nickname": "Author"},
        "video": {"duration": 12000},
    }
    provider = FavoritePages(
        folders=[
            {"collects_id_str": "100", "collects_name": "First", "total_number": 2},
            {"collects_id_str": "200", "collects_name": "Second", "total_number": 1},
        ],
        works={
            "100": [shared_work, {"aweme_id": "5678", "status": {"is_delete": True}}],
            "200": [shared_work],
        },
        is_complete=True,
    )
    monkeypatch.setattr(douyin_collector, "read_favorites_page", lambda *_: provider)
    snapshot = collector._fetch_in_context(object())

    assert snapshot.is_complete
    assert snapshot.invalid_count == 1
    assert [folder.platform_collection_id for folder in snapshot.collections] == ["100", "200"]
    assert len(snapshot.videos) == 1
    item = snapshot.videos[0]
    assert (item.platform_item_id, item.title, item.author, item.duration) == ("123456", "A saved video", "Author", 12)
    assert item.collection_ids == {"100", "200"}
    assert collector.get_sync_diagnostic()["outcome"] == "success"


def test_short_provider_listing_does_not_treat_unseen_works_as_invalid(monkeypatch):
    collector = DouyinCollector()
    provider = FavoritePages(
        folders=[{"collects_id_str": "100", "collects_name": "Saved", "total_number": 2}],
        works={"100": [{"aweme_id": "123456", "desc": "A saved video", "video": {"duration": 1000}}]},
        is_complete=True,
    )
    monkeypatch.setattr(douyin_collector, "read_favorites_page", lambda *_: provider)
    snapshot = collector._fetch_in_context(object())

    assert not snapshot.is_complete
    assert snapshot.videos[0].duration == 1
    assert snapshot.invalid_count == 0
    assert collector.get_sync_diagnostic()["outcome"] == "partial"


def test_web_response_error_records_safe_diagnostics(monkeypatch):
    collector = DouyinCollector()

    def read_failure(*_):
        raise DouyinFavoritesPageError("provider_status", 403)

    monkeypatch.setattr(douyin_collector, "read_favorites_page", read_failure)
    with pytest.raises(DouyinFavoritesPageError, match="provider_status"):
        collector._fetch_in_context(object())

    diagnostic = collector.get_sync_diagnostic()
    assert diagnostic["phase"] == "favorites_page"
    assert diagnostic["outcome"] == "failed"
    assert diagnostic["error_code"] == "provider_status"
    assert diagnostic["provider_status_code"] == 403
    assert diagnostic["module_id"] is None


def test_cached_snapshot_survives_live_sync_failure(monkeypatch):
    collector = DouyinCollector()
    cached = FavoriteScrapeSnapshot(
        collections=[FavoriteScrapedCollection("c1", "Cached", 1)],
        is_cached=True,
        is_complete=False,
    )
    monkeypatch.setattr(collector, "_snapshot_from_database", lambda: cached)

    class FakePlaywright:
        def __enter__(self):
            raise RuntimeError("browser unavailable")

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(douyin_collector, "sync_playwright", lambda: FakePlaywright())

    result = collector.scrape_favorites_sync()

    assert result is cached
    assert collector._snapshot is cached
    assert collector.status == "logged_in"
    assert collector.get_sync_diagnostic()["outcome"] == "fallback_cached"
