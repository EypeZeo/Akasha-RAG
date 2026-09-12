"""Resolve a Douyin video's own media through an isolated browser session.

The normal yt-dlp extractor cannot always obtain the web detail response with
exported cookies alone. This fallback lets the site load its normal page and
accepts media only from metadata explicitly belonging to the requested item.
"""
from __future__ import annotations

import json
import logging
import re
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from app.core.config import settings

logger = logging.getLogger(__name__)
_TIMEOUT_SECONDS = 30.0


def close_thread_local_browser() -> None:
    """
    历史遗留的钩子：曾用于回收每线程复用的 headless 浏览器。

    现在 resolve_douyin_media 每次都用 `with sync_playwright()` 开一个独立浏览器并在
    退出时关闭（Playwright sync API 跨线程复用不稳定，容易卡死），因此这里无需回收，
    保留为空实现只是为了兼容调用点。
    """
    return


_MAX_PAYLOAD_BYTES = 4 * 1024 * 1024
_MEDIA_DOMAINS = (
    "douyinvod.com", "douyin.com", "amemv.com", "bytecdn.cn",
    "bytedance.com", "bytedcdn.com", "pstatp.com", "ixigua.com",
    "snssdk.com", "ibytedtos.com", "zjcdn.com",
)


class DouyinMediaResolveError(RuntimeError):
    """The authenticated page did not expose verified media for this item."""


def _media_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value.startswith("//"):
        value = "https:" + value
    try:
        parts = urlsplit(value)
        hostname = (parts.hostname or "").lower()
        if (parts.scheme != "https" or parts.username or parts.password
                or parts.port not in (None, 443)):
            return None
    except ValueError:
        return None
    if any(hostname == domain or hostname.endswith("." + domain)
           for domain in _MEDIA_DOMAINS):
        return value
    return None


def _extract_media(payload: object, item_id: str) -> str | None:
    """Ignore recommendations and music; inspect only an exact item's video."""
    pending = [payload]
    visited = 0
    while pending and visited < 20_000:
        node = pending.pop()
        visited += 1
        if isinstance(node, list):
            pending.extend(node)
            continue
        if not isinstance(node, dict):
            continue
        node_id = node.get("aweme_id", node.get("awemeId"))
        if str(node_id) == item_id and isinstance(node.get("video"), dict):
            video = node["video"]
            addresses = [video.get(key) for key in (
                "play_addr", "playAddr", "play_addr_h264", "playAddrH264",
            )]
            rates = video.get("bit_rate", video.get("bitRate", video.get("bitrateInfo", [])))
            if isinstance(rates, list):
                for rate in rates:
                    if isinstance(rate, dict):
                        addresses.append(rate.get("play_addr", rate.get("playAddr", rate.get("PlayAddr"))))
            addresses.extend(video.get(key) for key in ("download_addr", "downloadAddr"))
            for address in addresses:
                if not isinstance(address, dict):
                    continue
                urls = address.get("url_list", address.get("urlList", address.get("UrlList", [])))
                if not isinstance(urls, list):
                    continue
                for value in urls:
                    if url := _media_url(value):
                        return url
        pending.extend(value for value in node.values() if isinstance(value, (dict, list)))
    return None


def _storage_state_path() -> Path | None:
    backend = Path(__file__).resolve().parents[2]
    configured = Path(settings.playwright_user_data_dir)
    for directory in (configured, backend / configured, backend / "app/storage/playwright_user_data"):
        path = directory / "state.json"
        if path.is_file():
            return path
    return None


def _browser_launch_kwargs() -> dict:
    """Mirror collector browser discovery without instantiating its singleton."""
    backend = Path(__file__).resolve().parents[2]
    configured = Path(settings.playwright_browsers_path)
    for base in (configured, backend / configured):
        # Deliberately duplicated rather than imported: importing douyin_collector
        # would instantiate its module-level singleton, which this helper exists to
        # avoid.  Kept in sync with douyin_collector._CHROMIUM_GLOBS by
        # test_chromium_discovery.py.
        for pattern in ("chromium-*/chrome-win/chrome.exe", "chromium-*/chrome-win64/chrome.exe"):
            for path in sorted(base.glob(pattern), reverse=True):
                if path.is_file():
                    return {"headless": True, "executable_path": str(path)}
    channel = settings.playwright_browser_channel.strip()
    if channel and channel != "chromium":
        return {"headless": True, "channel": channel}
    if sys.platform == "win32":
        return {"headless": True, "channel": "msedge"}
    return {"headless": True}


def resolve_douyin_media(platform_item_id: str) -> dict:
    """Return media URL, browser headers, and domain-scoped cookie records.

    Browser objects never leave the creating thread. No persistent profile is
    opened or updated, and full URLs/cookies are deliberately absent from errors.
    """
    if not re.fullmatch(r"[0-9]{1,30}", platform_item_id):
        raise DouyinMediaResolveError("抖音作品 ID 格式无效")
    deadline = time.monotonic() + _TIMEOUT_SECONDS

    def remaining_ms() -> int:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DouyinMediaResolveError("浏览器解析媒体超时，请稍后重试")
        return max(1, int(remaining * 1000))

    page_url = f"https://www.douyin.com/video/{platform_item_id}"
    found: list[str] = []
    browser = None
    context = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                **_browser_launch_kwargs(), timeout=remaining_ms(),
            )
            state_path = _storage_state_path()
            context = browser.new_context(
                **({"storage_state": str(state_path)} if state_path else {}),
                locale="zh-CN",
            )
            context.set_default_timeout(remaining_ms())
            page = context.new_page()

            def inspect_finished(request) -> None:
                if found or time.monotonic() >= deadline:
                    return
                parts = urlsplit(request.url)
                if (parts.hostname != "www.douyin.com"
                        or "/aweme/v1/web/" not in parts.path
                        or request.resource_type not in {"xhr", "fetch"}):
                    return
                try:
                    response = request.response()
                    if response is None or response.status != 200:
                        return
                    length = response.headers.get("content-length")
                    if length and int(length) > _MAX_PAYLOAD_BYTES:
                        return
                    body = response.body()
                    if len(body) > _MAX_PAYLOAD_BYTES:
                        return
                    if url := _extract_media(json.loads(body), platform_item_id):
                        found.append(url)
                except Exception:
                    # A failed optional response must not prevent another
                    # response or the page's hydration state from resolving.
                    return

            page.on("requestfinished", inspect_finished)
            try:
                page.goto(page_url, wait_until="domcontentloaded", timeout=remaining_ms())
            except PlaywrightTimeoutError:
                remaining_ms()

            while not found:
                remaining_ms()
                # SSR pages can contain the item without making a detail
                # request. Only JSON hydration scripts are parsed, never JS.
                scripts = page.locator(
                    'script#RENDER_DATA, script#_RENDER_DATA, script[type="application/json"]'
                ).evaluate_all(
                    """nodes => {
                        let budget = 4194304;
                        const result = [];
                        for (const node of nodes.slice(0, 20)) {
                            const text = node.textContent || '';
                            if (text.length > budget) continue;
                            result.push(text);
                            budget -= text.length;
                        }
                        return result;
                    }"""
                )
                for raw in scripts:
                    if not raw or len(raw) > _MAX_PAYLOAD_BYTES:
                        continue
                    try:
                        payload = json.loads(unquote(raw) if raw.lstrip().startswith("%") else raw)
                    except (ValueError, TypeError):
                        continue
                    if url := _extract_media(payload, platform_item_id):
                        found.append(url)
                        break
                if not found:
                    page.wait_for_timeout(min(250, remaining_ms()))

            media_url = found[0]
            result = {
                "url": media_url,
                "http_headers": {
                    "Referer": page_url,
                    "User-Agent": page.evaluate("navigator.userAgent"),
                },
                "cookies": context.cookies([media_url]),
            }
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass
            return result
    except DouyinMediaResolveError:
        raise
    except Exception as exc:
        # Playwright error messages may contain signed URLs. Keep diagnostics
        # useful without persisting those URLs or any credential material.
        raise DouyinMediaResolveError(
            f"浏览器未能解析当前作品媒体（{type(exc).__name__}），请检查抖音登录和网络"
        ) from None
