"""Offline regression coverage for changed, expired and incomplete QR images."""
import io
import threading
from unittest.mock import Mock

import pytest
from PIL import Image
from playwright.sync_api import sync_playwright

from app.services.zhihu_collector import ZhihuCollector, douyin_collector


@pytest.fixture
def collector():
    value = object.__new__(ZhihuCollector)
    value._lock = threading.RLock()
    value._cancel = threading.Event()
    value._qrcode_image_base64 = 'old'
    value._previous_qrcode = 'old'
    value.message = ''
    return value


def test_rotated_qr_is_published_only_after_two_matching_samples(collector, monkeypatch):
    monkeypatch.setattr(collector, '_qrcode_expired', lambda _page: False)
    captures = iter(['new-in-progress', 'new', 'new'])
    monkeypatch.setattr(collector, '_capture_qrcode', lambda _page: next(captures))
    collector._poll_qrcode(object())
    assert collector._qrcode_image_base64 is None
    collector._poll_qrcode(object())
    assert collector._qrcode_image_base64 is None
    collector._poll_qrcode(object())
    assert collector._qrcode_image_base64 == 'new'


def test_missing_renderer_clears_stale_qr(collector, monkeypatch):
    monkeypatch.setattr(collector, '_qrcode_expired', lambda _page: False)
    monkeypatch.setattr(collector, '_capture_qrcode', lambda _page: None)
    collector._poll_qrcode(object())
    assert collector._qrcode_image_base64 is None


def test_expired_qr_reloads_same_page_and_waits_for_complete_new_qr(collector, monkeypatch):
    calls = []

    class Page:
        def reload(self, **kwargs):
            assert collector._qrcode_image_base64 is None
            calls.append(kwargs)

    monkeypatch.setattr(collector, '_qrcode_expired', lambda _page: True)
    monkeypatch.setattr(collector, '_wait_for_qrcode', lambda page: calls.append(page))
    page = Page()
    collector._poll_qrcode(page)
    assert calls == [{'wait_until': 'domcontentloaded', 'timeout': 20_000}, page]


def test_cancel_prevents_qr_renewal(collector, monkeypatch):
    collector._cancel.set()
    monkeypatch.setattr(collector, '_qrcode_expired', lambda page: pytest.fail('cancelled session touched browser'))
    collector._poll_qrcode(object())


@pytest.mark.asyncio
@pytest.mark.parametrize('cancelled', [False, True])
async def test_restart_waits_off_loop_for_retiring_worker(collector, cancelled):
    thread = Mock()
    collector.status = 'expired'
    collector._thread = thread
    thread.is_alive.side_effect = [True, False]
    owner = threading.get_ident()

    def join(_timeout):
        assert threading.get_ident() != owner
        if cancelled:
            collector.status = 'idle'

    thread.join.side_effect = join
    assert await collector.wait_for_login_retirement() is (not cancelled)
    thread.join.assert_called_once_with(3.0)


@pytest.mark.parametrize('color', ['white', 'black', '#808080'])
def test_blank_stable_canvas_cannot_be_published(color):
    buffer = io.BytesIO()
    Image.new('RGB', (160, 160), color).save(buffer, format='PNG')

    class Element:
        def is_visible(self):
            return True

        def bounding_box(self):
            return {'width': 160, 'height': 160}

        def screenshot(self, **_kwargs):
            return buffer.getvalue()

    assert ZhihuCollector._capture_qr_element(Element(), is_canvas=True) is None


@pytest.fixture
def offline_page():
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(**douyin_collector._browser_launch_kwargs(headless=True))
        except Exception as exc:
            pytest.skip(f'local Chromium unavailable: {type(exc).__name__}')
        try:
            page = browser.new_page()
            page.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html', body='<html></html>'))
            page.goto('https://www.zhihu.com/signin')
            yield page
        finally:
            browser.close()


@pytest.mark.parametrize('markup,expected', [
    ('<p>二维码已过期</p><div class="Qrcode-container">请扫码</div>', False),
    ('<div class="Qrcode-container" style="display:none">二维码已过期</div>', False),
    ('<div class="Qrcode-container" style="visibility:hidden">二维码已过期</div>', False),
    ('<div class="Qrcode-container" style="opacity:0">二维码已过期</div>', False),
    ('<div class="Qrcode-container"><span style="display:none">二维码已过期</span>请扫码</div>', False),
    ('<div class="Qrcode-container">二维码已失效，请刷新</div>', True),
    ('<div class="Qrcode-container">点击刷新</div>', True),
    ('<div class="Qrcode-container">请在手机上确认登录</div>', False),
])
def test_only_visible_expiry_in_qr_container_triggers_renewal(offline_page, markup, expected):
    offline_page.set_content(markup)
    assert ZhihuCollector._qrcode_expired(offline_page) is expected
