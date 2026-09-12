import unittest
from unittest.mock import MagicMock, patch

from app.services.douyin_media_resolver import (
    DouyinMediaResolveError,
    _extract_media,
    _media_url,
    resolve_douyin_media,
)


class DouyinMediaResolverTests(unittest.TestCase):
    def test_recommendation_media_cannot_replace_target_item(self):
        payload = {"aweme_list": [
            {"aweme_id": "12", "video": {"play_addr": {"url_list": ["https://v.douyinvod.com/other.mp4"]}}},
            {"aweme_id": "34", "video": {"play_addr": {"url_list": ["https://v.douyinvod.com/target.mp4"]}}},
        ]}
        self.assertEqual(_extract_media(payload, "34"), "https://v.douyinvod.com/target.mp4")
        self.assertIsNone(_extract_media(payload, "56"))

    def test_music_track_is_not_narration_fallback(self):
        payload = {"aweme_id": "34", "video": {}, "music": {
            "play_addr": {"url_list": ["https://v.douyinvod.com/music.mp3"]},
        }}
        self.assertIsNone(_extract_media(payload, "34"))

    def test_camel_case_bitrate_and_protocol_relative_urls(self):
        payload = {"awemeDetail": {"awemeId": "34", "video": {
            "bitrateInfo": [{"PlayAddr": {"UrlList": ["//v.douyinvod.com/video.mp4"]}}],
        }}}
        self.assertEqual(_extract_media(payload, "34"), "https://v.douyinvod.com/video.mp4")

    def test_untrusted_media_destinations_rejected(self):
        for url in (
            "http://v.douyinvod.com/video", "https://localhost/video", "file:///tmp/a",
            "https://v.douyinvod.com.evil.example/video", "https://user:pass@v.douyinvod.com/a",
            "https://v.douyinvod.com:8443/video", "https://[broken/video",
        ):
            with self.subTest(url=url):
                self.assertIsNone(_media_url(url))

    def test_invalid_item_id_rejected_before_browser_launch(self):
        with self.assertRaises(DouyinMediaResolveError):
            resolve_douyin_media("../123")

    def test_verified_response_returns_scoped_cookies_and_closes_browser(self):
        manager = MagicMock()
        playwright = manager.__enter__.return_value
        browser = playwright.chromium.launch.return_value
        context = browser.new_context.return_value
        page = context.new_page.return_value
        page.evaluate.return_value = "actual-browser-agent"
        response = MagicMock(status=200, headers={})
        response.body.return_value = b'{"aweme_detail":{"aweme_id":"34","video":{"play_addr":{"url_list":["https://v.douyinvod.com/item.mp4"]}}}}'
        request = MagicMock(url="https://www.douyin.com/aweme/v1/web/aweme/detail/", resource_type="fetch")
        request.response.return_value = response
        page.goto.side_effect = lambda *a, **k: page.on.call_args.args[1](request)
        context.cookies.return_value = [{"name": "example", "domain": ".douyinvod.com", "path": "/"}]
        with patch("app.services.douyin_media_resolver.sync_playwright", return_value=manager):
            result = resolve_douyin_media("34")
        self.assertEqual(result["url"], "https://v.douyinvod.com/item.mp4")
        self.assertNotIn("Cookie", result["http_headers"])
        context.cookies.assert_called_once_with([result["url"]])
        context.close.assert_called_once()
        browser.close.assert_called_once()
        context.storage_state.assert_not_called()

    def test_browser_failure_closes_resources_and_redacts_url(self):
        manager = MagicMock()
        browser = manager.__enter__.return_value.chromium.launch.return_value
        context = browser.new_context.return_value
        context.new_page.return_value.goto.side_effect = RuntimeError("https://example.invalid/?token=SECRET")
        with patch("app.services.douyin_media_resolver.sync_playwright", return_value=manager):
            with self.assertRaises(DouyinMediaResolveError) as caught:
                resolve_douyin_media("34")
        self.assertNotIn("SECRET", str(caught.exception))
        # 异常路径由 `with sync_playwright()` 退出时统一清理浏览器


if __name__ == "__main__":
    unittest.main()
