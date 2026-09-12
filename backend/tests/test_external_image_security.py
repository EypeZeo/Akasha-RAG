from __future__ import annotations

from types import SimpleNamespace

from app.api.routes.auth import _display_profile
from app.core.external_urls import safe_platform_image_url
from app.services.vision_service import VisionService


def test_platform_image_url_only_accepts_trusted_https_hosts():
    assert safe_platform_image_url("bilibili", "https://i0.hdslb.com/bfs/face/a.jpg?x=1") == "https://i0.hdslb.com/bfs/face/a.jpg?x=1"
    assert safe_platform_image_url("douyin", "https://p3-sign.douyinpic.com/tos-cn/a.webp") == "https://p3-sign.douyinpic.com/tos-cn/a.webp"


def test_platform_image_url_rejects_ssrf_and_suffix_bypasses():
    for value in (
        "http://i0.hdslb.com/face.jpg",
        "https://evilhdslb.com/face.jpg",
        "https://i0.hdslb.com.evil.example/face.jpg",
        "https://127.0.0.1/face.jpg",
        "https://[::1]/face.jpg",
        "https://user@i0.hdslb.com/face.jpg",
        "https://i0.hdslb.com:8443/face.jpg",
    ):
        assert safe_platform_image_url("bilibili", value) == ""


def test_auth_profile_does_not_return_an_untrusted_legacy_avatar():
    nickname, avatar_url = _display_profile(
        "bilibili",
        SimpleNamespace(nickname="cached", avatar_url="https://127.0.0.1/face.png"),
        "",
        "",
    )
    assert nickname == "cached"
    assert avatar_url == ""


def test_vision_rejects_an_untrusted_fallback_cover_without_request(monkeypatch):
    service = VisionService()

    class NotFound:
        status_code = 404

    monkeypatch.setattr("app.services.vision_service.requests.get", lambda *args, **kwargs: NotFound())
    assert service.fetch_note_image_urls("123", "https://127.0.0.1/private.png") == []


def test_vision_rechecks_every_redirect_target(monkeypatch):
    service = VisionService()
    requested: list[str] = []

    class Redirect:
        status_code = 302
        is_redirect = True
        is_permanent_redirect = False

        def __init__(self):
            self.headers = {"Location": "https://127.0.0.1/private.png"}

        def close(self):
            pass

    def fake_get(url, **kwargs):
        requested.append(url)
        return Redirect()

    monkeypatch.setattr("app.services.vision_service.requests.get", fake_get)
    assert service._download_trusted_image("https://p3-sign.douyinpic.com/a.png", {}) is None
    assert requested == ["https://p3-sign.douyinpic.com/a.png"]
