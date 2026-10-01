"""Current-user profile checks reuse the authenticated me response."""
import json
import threading
from pathlib import Path
from unittest.mock import Mock

import pytest
from playwright.sync_api import sync_playwright

from app.services.zhihu_collector import ZhihuCollector


AVATAR = "https://pic1.zhimg.com/current-account.jpg"


@pytest.fixture
def collector(tmp_path):
    value = object.__new__(ZhihuCollector)
    value._lock = threading.RLock()
    value._cancel = threading.Event()
    value._profile = {}
    value._profile_path = tmp_path / "zhihu_profile.json"
    value._qrcode_image_base64 = None
    value.status = "logged_in"
    value.message = ""
    return value


def check_me(collector, payload, *, status=200, cookies=True):
    context = Mock()
    context.cookies.return_value = [{"name": "z_c0", "value": "session"}] if cookies else []
    page = Mock()
    page.evaluate.return_value = {"status": status, "text": json.dumps(payload)}
    return collector._page_logged_in(page, context), page


@pytest.mark.parametrize("wrapped", [False, True])
def test_me_response_repairs_nickname_without_another_network_request(collector, monkeypatch, wrapped):
    saved = Mock()
    monkeypatch.setattr("app.services.zhihu_collector.write_json", saved)
    collector._profile = {"avatar_url": AVATAR}
    payload = {"id": "self", "name": "  知乎网名  ", "avatar_url": AVATAR}
    valid, page = check_me(collector, {"data": payload} if wrapped else payload)
    assert valid
    assert collector.get_status()["nickname"] == "知乎网名"
    assert collector.get_status()["avatar_url"] == AVATAR
    page.evaluate.assert_called_once()
    assert "'/api/v4/me'" in page.evaluate.call_args.args[0]
    saved.assert_called_once_with(collector._profile_path, collector._profile)


def test_current_profile_refresh_retains_good_fields_when_upstream_is_incomplete(collector, monkeypatch):
    saved = Mock()
    monkeypatch.setattr("app.services.zhihu_collector.write_json", saved)
    collector._profile = {"nickname": "本人", "avatar_url": AVATAR}
    assert check_me(collector, {"id": "self", "name": "", "avatar_url": ""})[0]
    assert collector._profile == {"nickname": "本人", "avatar_url": AVATAR}
    saved.assert_not_called()


@pytest.mark.parametrize("payload,status", [
    ({"name": "Other", "avatar_url": AVATAR}, 401),
    ({"error": {"name": "Other", "avatar_url": AVATAR}}, 200),
    ({"data": []}, 200),
    ([], 200),
])
def test_rejected_or_malformed_me_response_cannot_change_display_cache(collector, monkeypatch, payload, status):
    saved = Mock()
    monkeypatch.setattr("app.services.zhihu_collector.write_json", saved)
    assert not check_me(collector, payload, status=status)[0]
    assert collector._profile == {}
    saved.assert_not_called()


def test_no_auth_cookie_skips_me_request(collector):
    valid, page = check_me(collector, {"id": "self", "name": "本人"}, cookies=False)
    assert not valid
    page.evaluate.assert_not_called()


def test_profile_cache_failure_does_not_turn_login_into_failure(collector, monkeypatch):
    monkeypatch.setattr("app.services.zhihu_collector.write_json", Mock(side_effect=OSError("read only")))
    assert check_me(collector, {"id": "self", "name": "本人", "avatar_url": AVATAR})[0]
    assert collector._profile == {"nickname": "本人", "avatar_url": AVATAR}


def test_late_profile_cannot_restore_cancelled_account(collector, monkeypatch):
    saved = Mock()
    monkeypatch.setattr("app.services.zhihu_collector.write_json", saved)
    collector._cancel.set()
    collector._remember_profile({"nickname": "Old account", "avatar_url": AVATAR})
    assert collector._profile == {}
    saved.assert_not_called()


def test_me_avatar_template_is_supported_and_unsafe_urls_are_rejected(collector, monkeypatch):
    monkeypatch.setattr("app.services.zhihu_collector.write_json", Mock())
    assert check_me(collector, {"id": "self", "name": "本人", "avatar_url_template": "https://pic1.zhimg.com/self_{size}.jpg"})[0]
    assert collector._profile["avatar_url"] == "https://pic1.zhimg.com/self_xl.jpg"
    assert ZhihuCollector._safe_profile({"nickname": "N" * 200, "avatar_url": "https://zhimg.com.evil.test/avatar"}) == {"nickname": "N" * 128}
    assert ZhihuCollector._safe_profile({"nickname": {"name": "bad"}, "avatar_url": "https://127.0.0.1/avatar"}) == {}


def test_loading_display_cache_sanitizes_legacy_values(collector, monkeypatch):
    monkeypatch.setattr("app.services.zhihu_collector.read_json", lambda _: {"nickname": ["bad"], "avatar_url": "http://pic1.zhimg.com/insecure"})
    assert collector._load_profile() == {}


@pytest.fixture(scope="module")
def offline_page():
    """Exercise DOM selectors in Chromium without accessing Zhihu."""
    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).is_file():
            pytest.skip("Chromium is not installed")
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body="<html></html>"))
        page.goto("https://www.zhihu.com/")
        yield page
        browser.close()


def test_dom_fallback_reads_only_current_header_account(offline_page):
    offline_page.set_content(f'''
        <article><img class="Avatar" src="https://pic1.zhimg.com/author.jpg" width="40" height="40" alt="另一个作者"></article>
        <div data-za-detail-view-path="UserProfile">个人主页</div>
        <header class="AppHeader"><img class="AppHeader-profileAvatar Avatar" src="{AVATAR}" width="40" height="40" alt="本人网名"></header>
    ''')
    assert ZhihuCollector._extract_profile(offline_page) == {"nickname": "本人网名", "avatar_url": AVATAR}


@pytest.mark.parametrize("alt", ["头像", "我的头像", "用户头像", "Avatar"])
def test_dom_avatar_placeholder_is_not_used_as_nickname(offline_page, alt):
    offline_page.set_content(f'<header class="AppHeader"><img class="AppHeader-profileAvatar" src="{AVATAR}" width="40" height="40" alt="{alt}"></header>')
    assert ZhihuCollector._extract_profile(offline_page) == {"avatar_url": AVATAR}


def test_dom_without_personal_header_does_not_capture_author_or_instrumentation_label(offline_page):
    offline_page.set_content(f'''
        <div data-za-detail-view-path="UserProfile">个人主页</div>
        <article><img class="Avatar" src="{AVATAR}" width="40" height="40" alt="作者网名"></article>
        <h1 class="ProfileHeader-name">其他用户</h1>
    ''')
    assert ZhihuCollector._extract_profile(offline_page) == {}


def test_hidden_header_avatar_is_ignored(offline_page):
    offline_page.set_content(f'<header class="AppHeader" style="display:none"><img class="AppHeader-profileAvatar" src="{AVATAR}" width="40" height="40" alt="旧用户"></header>')
    assert ZhihuCollector._extract_profile(offline_page) == {}
