from app.services.douyin_collector import DouyinCollector, FavoriteScrapeSnapshot, FavoriteScrapedCollection


def test_cached_snapshot_survives_live_sync_failure(monkeypatch):
    collector = DouyinCollector()
    collector._snapshot = None
    cached = FavoriteScrapeSnapshot(
        collections=[FavoriteScrapedCollection("c1", "Cached", 1)],
        videos=[],
    )
    monkeypatch.setattr(collector, "_snapshot_from_database", lambda: cached)

    class FakePlaywright:
        def __enter__(self):
            raise RuntimeError("provider runtime changed")

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("app.services.douyin_collector.sync_playwright", lambda: FakePlaywright())

    result = collector.scrape_favorites_sync()

    assert result is cached
    assert collector._snapshot is cached
    assert collector.status == "logged_in"
    assert "本地快照" in collector.message
