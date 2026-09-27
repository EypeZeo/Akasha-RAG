from app.services.douyin_collector import (
    DouyinCollector,
    FavoriteScrapeSnapshot,
    FavoriteScrapedCollection,
)


class _FakeLocator:
    def is_visible(self, timeout=0):
        return False


class _FakePage:
    def __init__(self, module_id=None, provider_result=None):
        self.module_id = module_id
        self.provider_result = provider_result
        self.evaluate_scripts = []
        self.reload_count = 0

    def goto(self, *args, **kwargs):
        return None

    def reload(self, *args, **kwargs):
        self.reload_count += 1

    def locator(self, *args, **kwargs):
        return _FakeLocator()

    def evaluate(self, script, *args):
        self.evaluate_scripts.append(script)
        if "Object.entries(req.m)" in script:
            return self.module_id
        return self.provider_result


def _run_fake_fetch(monkeypatch, page):
    monkeypatch.setattr("app.services.douyin_collector.time.sleep", lambda _: None)
    collector = DouyinCollector()
    try:
        snapshot = collector._fetch_in_context(page)
    except RuntimeError:
        snapshot = None
    return collector, snapshot


def test_missing_module_records_safe_diagnostic(monkeypatch):
    collector, snapshot = _run_fake_fetch(monkeypatch, _FakePage())

    assert snapshot is None
    assert collector.get_sync_diagnostic() == {
        "phase": "module_discovery",
        "outcome": "failed",
        "error_code": "module_not_found",
        "module_id": None,
        "export_keys": [],
        "list_export_found": False,
        "video_export_found": False,
        "provider_status_code": None,
    }


def test_missing_exports_records_module_shape_without_provider_payload(monkeypatch):
    page = _FakePage(
        module_id="module-42",
        provider_result={
            "ok": False,
            "error": "bad_exports",
            "exportKeys": ["So", "metadata"],
            "listExportFound": False,
            "videoExportFound": True,
        },
    )

    collector, snapshot = _run_fake_fetch(monkeypatch, page)

    assert snapshot is None
    diagnostic = collector.get_sync_diagnostic()
    assert diagnostic["error_code"] == "bad_exports"
    assert diagnostic["module_id"] == "module-42"
    assert diagnostic["export_keys"] == ["So", "metadata"]
    assert diagnostic["list_export_found"] is False
    assert diagnostic["video_export_found"] is True


def test_thrown_provider_call_records_sanitized_failure(monkeypatch):
    page = _FakePage(
        module_id="module-42",
        provider_result={
            "ok": False,
            "error": "provider_call_threw",
            "exportKeys": ["list", "videos"],
            "listExportFound": True,
            "videoExportFound": True,
            "statusCode": None,
        },
    )

    collector, snapshot = _run_fake_fetch(monkeypatch, page)

    assert snapshot is None
    diagnostic = collector.get_sync_diagnostic()
    assert diagnostic["error_code"] == "provider_call_threw"
    assert diagnostic["provider_status_code"] is None
    assert "cookie" not in str(diagnostic).lower()
    assert "https://" not in str(diagnostic)


def test_non_zero_provider_status_is_reported(monkeypatch):
    page = _FakePage(
        module_id="module-42",
        provider_result={
            "ok": False,
            "error": "list_status",
            "statusCode": 403,
            "exportKeys": ["list", "videos"],
            "listExportFound": True,
            "videoExportFound": True,
        },
    )

    collector, snapshot = _run_fake_fetch(monkeypatch, page)

    assert snapshot is None
    assert collector.get_sync_diagnostic()["provider_status_code"] == 403


def test_successful_paginated_provider_result_is_normalized(monkeypatch):
    page = _FakePage(
        module_id="module-42",
        provider_result={
            "ok": True,
            "statusCode": 0,
            "exportKeys": ["list", "videos"],
            "listExportFound": True,
            "videoExportFound": True,
            "collections": [{
                "collectionFolderId": "collection-1",
                "collectionFolderName": "Saved",
                "videoTotal": 1,
            }],
            "itemsByCollection": {
                "collection-1": [{
                    "awemeId": "123456",
                    "title": "A saved video",
                    "author": "Author",
                    "durationMs": 12_000,
                }],
            },
            "invalidCount": 0,
        },
    )

    collector, snapshot = _run_fake_fetch(monkeypatch, page)

    assert snapshot is not None
    assert snapshot.collections[0].platform_collection_id == "collection-1"
    assert snapshot.videos[0].platform_item_id == "123456"
    assert snapshot.videos[0].duration == 12
    assert collector.get_sync_diagnostic()["outcome"] == "success"
    assert any(".call(api" in script for script in page.evaluate_scripts)
    fetch_script = next(script for script in page.evaluate_scripts if ".call(api" in script)
    assert "while (guard < 30 && collections.length < 100)" in fetch_script
    assert "while (cG < 120 && rows.length < 500)" in fetch_script


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
