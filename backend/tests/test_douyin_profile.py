"""Current account display data stays independent from authentication."""
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from playwright.sync_api import sync_playwright

from app.services.douyin_collector import DouyinCollector


SELF_URL = "https://www.douyin.com/aweme/v1/web/user/profile/self/"
AVATAR = "https://p3-sign.douyinpic.com/account.webp?signature=example"


@pytest.fixture
def collector():
    result = object.__new__(DouyinCollector)
    result._logout_requested = threading.Event()
    result._profile = {}
    result._save_profile = Mock()
    return result


def emit_response(collector, url=SELF_URL, payload=None, status=200):
    page = Mock()
    collector._watch_profile(page)
    page.on.assert_called_once()
    event, callback = page.on.call_args.args
    assert event == "response"
    response = SimpleNamespace(url=url, status=status, json=Mock(return_value=payload))
    callback(response)
    return response


def test_current_user_response_prefers_trusted_avatar_and_persists_nickname(collector):
    emit_response(collector, payload={
        "status_code": 0,
        "user": {"nickname": "  我的账号  ",
                 "avatar_larger": {"url_list": ["https://127.0.0.1/avatar", AVATAR]},
                 "avatar_thumb": {"url_list": ["https://p3-sign.douyinpic.com/thumb.webp"]}},
    })
    assert collector.get_profile() == {"nickname": "我的账号", "avatar_url": AVATAR}
    collector._save_profile.assert_called_once()


@pytest.mark.parametrize("url,status,payload", [
    ("https://www.douyin.com/aweme/v1/web/user/detail/?sec_user_id=author", 200, {}),
    ("https://www.douyin.com.evil.test/aweme/v1/web/user/profile/self/", 200, {}),
    ("http://www.douyin.com/aweme/v1/web/user/profile/self/", 200, {}),
    ("https://www.douyin.com:444/aweme/v1/web/user/profile/self/", 200, {}),
    ("https://user:password@www.douyin.com/aweme/v1/web/user/profile/self/", 200, {}),
    (SELF_URL, 401, {}),
    (SELF_URL, 200, {"status_code": 8, "user": {"nickname": "Guest"}}),
    (SELF_URL, 200, {"status_code": 0, "user": "malformed"}),
])
def test_unrelated_or_invalid_responses_do_not_change_account(collector, url, status, payload):
    emit_response(collector, url=url, status=status, payload=payload)
    assert collector.get_profile() == {}
    collector._save_profile.assert_not_called()


def test_late_profile_response_cannot_restore_logged_out_account(collector):
    collector._logout_requested.set()
    response = emit_response(collector, payload={
        "status_code": 0, "user": {"nickname": "Old account", "avatar_thumb": {"url_list": [AVATAR]}},
    })
    response.json.assert_not_called()
    assert collector.get_profile() == {}


def test_render_retry_collects_delayed_profile_and_does_not_repeat_disk_writes(collector):
    page = Mock()
    collector._extract_profile = Mock(side_effect=[{}, {"nickname": "本人"}, {"nickname": "本人", "avatar_url": AVATAR}])
    collector._refresh_profile(page)
    assert collector.get_profile() == {"nickname": "本人", "avatar_url": AVATAR}
    assert page.wait_for_timeout.call_count == 2
    collector._remember_profile(collector.get_profile())
    assert collector._save_profile.call_count == 2


def test_profile_timeout_or_closed_page_never_changes_login_status(collector):
    collector.status = "syncing"
    collector._extract_profile = Mock(return_value={})
    page = Mock()
    page.wait_for_timeout.side_effect = RuntimeError("page closed")
    collector._refresh_profile(page)
    assert collector.status == "syncing"
    collector._save_profile.assert_not_called()


def test_missing_profile_has_a_fixed_deadline_and_retains_previous_account(collector, monkeypatch):
    collector._profile = {"nickname": "本人", "avatar_url": AVATAR}
    collector._extract_profile = Mock(return_value={})
    clock = iter([0, 0.5, 2.0])
    monkeypatch.setattr("app.services.douyin_collector.time.monotonic", lambda: next(clock))
    page = Mock()
    collector._refresh_profile(page, timeout=1)
    assert page.wait_for_timeout.call_count == 1
    assert collector.get_profile() == {"nickname": "本人", "avatar_url": AVATAR}
    collector._save_profile.assert_not_called()


def test_profile_refresh_runs_after_self_page_navigation(collector, monkeypatch):
    page = Mock()
    steps = []
    page.goto.side_effect = lambda url, **kwargs: steps.append(url)
    collector._refresh_profile = Mock(side_effect=lambda target: steps.append("profile"))
    collector._set_sync_diagnostic = Mock()
    page.evaluate.return_value = None
    monkeypatch.setattr("app.services.douyin_collector.time.sleep", lambda _: None)
    with pytest.raises(RuntimeError):
        collector._fetch_in_context(page)
    assert steps[0].endswith("/user/self?showTab=favorite_collection")
    assert steps[1] == "profile"


@pytest.fixture(scope="module")
def offline_page():
    """Browser tests need an installed Chromium; every request is intercepted."""
    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).is_file():
            pytest.skip("Chromium not installed; unit tests still validate response and lifecycle paths")
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body="<html></html>"))
        page.goto("https://www.douyin.com/user/self")
        yield page
        browser.close()


def test_dom_does_not_mistake_author_cover_or_metadata_for_current_user(offline_page):
    offline_page.set_content(f'''
        <meta property="og:image" content="{AVATAR}">
        <meta property="og:title" content="视频标题">
        <article><div data-e2e="user-info"><img src="{AVATAR}" width="40" height="40">
        <span data-e2e="user-name">作者</span></div></article>
        <img src="{AVATAR}" alt="avatar" width="100" height="100">
        <a href="/user/author">作者昵称</a>
    ''')
    assert DouyinCollector._extract_profile(offline_page) == {}


@pytest.mark.parametrize("avatar_html", [
    f'<img data-e2e="user-avatar" src="{AVATAR}" width="40" height="40">',
    f'<div data-e2e="user-avatar"><img src="{AVATAR}" width="40" height="40"></div>',
    f'<div data-e2e="user-avatar" style="width:40px;height:40px;background-image:url(\'{AVATAR}\')"></div>',
])
def test_current_account_dom_supports_direct_image_nested_image_and_background(offline_page, avatar_html):
    offline_page.set_content(f'{avatar_html}<span data-e2e="user-name">本人</span>')
    assert DouyinCollector._extract_profile(offline_page) == {"nickname": "本人", "avatar_url": AVATAR}


def test_current_account_dom_ignores_hidden_and_unsafe_avatar(offline_page):
    offline_page.set_content(f'''
        <img data-e2e="user-avatar" style="display:none" src="{AVATAR}">
        <img data-e2e="user-avatar" src="https://127.0.0.1/avatar" width="40" height="40">
        <span data-e2e="user-name">本人</span>
    ''')
    assert DouyinCollector._extract_profile(offline_page) == {"nickname": "本人"}


def test_unsafe_first_current_account_avatar_does_not_hide_later_trusted_one(offline_page):
    offline_page.set_content(f'''
        <img data-e2e="user-avatar" src="https://127.0.0.1/placeholder" width="40" height="40">
        <img data-e2e="user-avatar" src="{AVATAR}" width="40" height="40">
        <span data-e2e="user-name">本人</span>
    ''')
    assert DouyinCollector._extract_profile(offline_page) == {"nickname": "本人", "avatar_url": AVATAR}


def test_async_current_account_render_can_repair_missing_profile(offline_page, collector):
    offline_page.set_content("<div id='account'></div>")
    offline_page.evaluate("""url => setTimeout(() => {
        document.getElementById('account').innerHTML =
          `<img data-e2e="user-avatar" src="${url}" width="40" height="40"><span data-e2e="user-name">本人</span>`;
    }, 100)""", AVATAR)
    collector._refresh_profile(offline_page, timeout=1)
    assert collector.get_profile() == {"nickname": "本人", "avatar_url": AVATAR}


def test_redirected_user_page_is_accepted_only_for_confirmed_current_account(offline_page, collector):
    offline_page.goto("https://www.douyin.com/user/known-self")
    offline_page.set_content(f'<img data-e2e="user-avatar" src="{AVATAR}" width="40" height="40"><span data-e2e="user-name">本人</span>')
    assert DouyinCollector._extract_profile(offline_page) == {}
    emit_response(collector, payload={"status_code": 0, "user": {"sec_uid": "known-self"}})
    collector._refresh_profile(offline_page, timeout=0)
    assert collector.get_profile() == {"nickname": "本人", "avatar_url": AVATAR}
    assert DouyinCollector._extract_profile(offline_page, "someone-else") == {}
    offline_page.goto("https://www.douyin.com/user/self")
