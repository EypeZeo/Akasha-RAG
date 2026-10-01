"""Douyin QR lifecycle owns browser calls and never republishes retired codes."""
import asyncio
import threading
from unittest.mock import Mock

import pytest
from playwright.sync_api import sync_playwright

from app.services.douyin_collector import DouyinCollector


@pytest.fixture
def collector():
    value = object.__new__(DouyinCollector)
    value.status = "pending"
    value.message = "scan"
    value._qr_image_base64 = "old"
    value._qr_error = None
    value._qr_ready_event = threading.Event()
    value._qr_ready_event.set()
    value._qr_refresh_requested = threading.Event()
    value._logout_requested = threading.Event()
    value._profile_cleanup_pending = threading.Event()
    value._lock = threading.Lock()
    value._active_context = Mock()
    value._active_page = Mock()
    value._login_task = None
    value._snapshot = None
    value._check_saved_login = Mock(return_value=False)
    return value


def test_api_refresh_only_signals_worker(collector):
    page = collector._active_page
    assert collector.refresh_qrcode() is None
    assert collector._qr_refresh_requested.is_set()
    assert not collector._qr_ready_event.is_set()
    assert collector.get_qrcode() is None
    assert not page.mock_calls


@pytest.mark.asyncio
async def test_expired_session_waits_for_worker_cleanup_before_new_login(collector):
    collector.status = 'expired'
    collector._lock.acquire()

    async def retire():
        await asyncio.sleep(0.01)
        collector._lock.release()

    task = asyncio.create_task(retire())
    assert await collector.wait_for_login_retirement(timeout=0.5)
    await task


@pytest.mark.asyncio
async def test_cancel_while_waiting_for_retirement_cannot_restart_login(collector):
    collector.status = 'expired'
    collector._lock.acquire()

    async def cancel():
        await asyncio.sleep(0.01)
        collector.status = 'idle'
        collector._lock.release()

    task = asyncio.create_task(cancel())
    assert not await collector.wait_for_login_retirement(timeout=0.5)
    await task


@pytest.mark.parametrize("status", ["syncing", "logged_in", "idle", "expired"])
def test_refresh_cannot_touch_completed_or_expired_session(collector, status):
    collector.status = status
    assert collector.refresh_qrcode() is None
    assert not collector._qr_refresh_requested.is_set()
    assert not collector._active_page.mock_calls


@pytest.mark.asyncio
async def test_start_clears_old_readiness_before_worker_runs(collector):
    collector._login_flow = Mock(return_value=asyncio.sleep(0))
    assert collector.start_login()[0]
    assert collector.get_qrcode() is None
    assert not collector._qr_ready_event.is_set()
    await collector._login_task


def test_rotation_replaces_image_without_browser_refresh(collector):
    collector._extract_qrcode_from_page = Mock(side_effect=["first", "rotated"])
    collector._update_qrcode(collector._active_page)
    assert collector.get_qrcode() == "first"
    collector._update_qrcode(collector._active_page)
    assert collector.get_qrcode() == "rotated"
    assert collector._qr_ready_event.is_set()


@pytest.mark.parametrize("signal", ["cancel", "refresh"])
def test_inflight_capture_never_overwrites_cancel_or_queued_refresh(collector, signal):
    def capture(*args, **kwargs):
        if signal == "cancel":
            collector.cancel_login()
        else:
            collector.refresh_qrcode()
        return "late-old-image"
    collector._extract_qrcode_from_page = capture
    collector._update_qrcode(collector._active_page)
    assert collector.get_qrcode() is None


def test_worker_executes_refresh_then_publishes_new_image(collector):
    page = collector._active_page
    context = collector._active_context
    context.cookies.side_effect = [[], [{"name": "sessionid"}]]
    collector._extract_qrcode_from_page = Mock(return_value="new")
    collector.refresh_qrcode()
    assert collector._wait_for_scan(context, page)
    page.locator.return_value.first.click.assert_called_once_with(timeout=2000)
    assert collector.get_qrcode() == "new"
    assert not collector._qr_refresh_requested.is_set()


def test_worker_reload_fallback_is_bounded_to_own_page(collector):
    page = collector._active_page
    page.locator.return_value.first.is_visible.return_value = False
    collector._active_context.cookies.side_effect = [[], [{"name": "sid_guard"}]]
    collector._extract_qrcode_from_page = Mock(return_value="new")
    collector.refresh_qrcode()
    assert collector._wait_for_scan(collector._active_context, page)
    page.reload.assert_called_once_with(wait_until="domcontentloaded", timeout=15000)


def test_timeout_marks_expired_and_discards_old_code(collector):
    assert not collector._wait_for_scan(collector._active_context, collector._active_page, timeout=0)
    assert collector.status == "expired"
    assert collector.get_qrcode() is None
    assert collector._qr_ready_event.is_set()


def test_cancellation_does_not_turn_into_expiration(collector):
    collector.cancel_login()
    assert not collector._wait_for_scan(Mock(), Mock(), timeout=0)
    assert collector.status == "idle"
    assert collector.get_qrcode() is None


@pytest.fixture(scope="module")
def page():
    with sync_playwright() as playwright:
        from pathlib import Path
        if not Path(playwright.chromium.executable_path).exists():
            pytest.skip("Chromium not installed")
        browser = playwright.chromium.launch(headless=True)
        value = browser.new_page()
        value.route("**/*", lambda route: route.fulfill(status=200, body="offline"))
        yield value
        browser.close()


def qr_fixture(page, where="#animate_qrcode_container", blank=False, centered=False):
    page.set_content('<div id="animate_qrcode_container"></div><div id="elsewhere"></div>')
    return page.evaluate("""({where, blank, centered}) => {
        const el = document.createElement('canvas'); el.width = 180; el.height = 180;
        const ctx = el.getContext('2d'); ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, 180, 180);
        ctx.fillStyle = '#000';
        if (centered) ctx.fillRect(70, 70, 40, 40);
        else if (!blank) for (let y=15; y<165; y+=5) for (let x=15; x<165; x+=5)
            if ((x+y)%10) ctx.fillRect(x,y,5,5);
        document.querySelector(where).appendChild(el);
        return el.toDataURL('image/png');
    }""", {"where": where, "blank": blank, "centered": centered})


@pytest.mark.parametrize("kind", ["blank", "centered", "unrelated", "hidden"])
def test_offline_loading_and_unrelated_images_are_not_qr(page, kind):
    qr_fixture(page, where="#elsewhere" if kind == "unrelated" else "#animate_qrcode_container",
               blank=kind == "blank", centered=kind == "centered")
    if kind == "hidden":
        page.eval_on_selector('canvas', "el => el.style.display = 'none'")
    assert DouyinCollector._extract_qrcode_from_page(page, timeout=150) is None


def test_offline_matrix_inside_login_container_is_captured_and_rotated(page):
    first = qr_fixture(page)
    assert DouyinCollector._extract_qrcode_from_page(page, timeout=150) == first
    rotated = page.eval_on_selector('canvas', """el => {
        el.getContext('2d').fillRect(0,0,5,5); return el.toDataURL('image/png');
    }""")
    assert rotated != first
    assert DouyinCollector._extract_qrcode_from_page(page, timeout=150) == rotated


def test_offline_decoded_image_uses_original_data_url(page):
    expected = qr_fixture(page)
    page.eval_on_selector('canvas', """el => {
        const img = document.createElement('img');
        img.width = 180; img.height = 180; img.src = el.toDataURL('image/png');
        el.replaceWith(img);
    }""")
    assert DouyinCollector._extract_qrcode_from_page(page, timeout=500) == expected


def test_loading_gap_removes_retired_image(collector):
    collector._extract_qrcode_from_page = Mock(return_value=None)
    collector._update_qrcode(collector._active_page)
    assert collector.get_qrcode() is None
    assert not collector._qr_ready_event.is_set()


def test_offline_tainted_canvas_can_capture_valid_cross_origin_matrix(page):
    import base64
    data = qr_fixture(page)
    png = base64.b64decode(data.split(',', 1)[1])
    page.unroute("**/*")
    page.route("**/*", lambda route: route.fulfill(status=200, content_type="image/png", body=png)
               if route.request.url.endswith('qr.png') else route.fulfill(status=200, body="offline"))
    page.goto('https://offline.example.test')
    page.set_content('<div id="animate_qrcode_container"><canvas width="180" height="180"></canvas></div>')
    page.evaluate("""async () => {
        const img = new Image(); img.src = 'https://other.example.test/qr.png'; await img.decode();
        document.querySelector('canvas').getContext('2d').drawImage(img, 0, 0);
    }""")
    with pytest.raises(Exception, match='Tainted|tainted'):
        page.eval_on_selector('canvas', "el => el.toDataURL()")
    actual = DouyinCollector._extract_qrcode_from_page(page, timeout=500)
    assert actual and actual.startswith('data:image/png;base64,')
