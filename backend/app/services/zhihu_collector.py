"""知乎收藏夹采集器。

知乎没有在本项目中承诺稳定的公开收藏夹 SDK，因此这里采用浏览器会话作为
唯一登录边界：登录阶段使用无头 Chromium 截取二维码，登录态使用本机安全存储
保存；同步时在同源页面内用 ``fetch`` 请求收藏夹接口，避免把 Cookie 暴露给
httpx 或日志。

这个模块只负责把知乎收藏转换成现有的 ``FavoriteScrapeSnapshot``。入库、正文
门禁、向量化仍由现有 FavoritesService/KnowledgeService 负责，避免引入第二套
生产流水线。
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import threading
import time
import base64
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from playwright.sync_api import sync_playwright

from app.core.config import settings
from app.core.secure_storage import delete_json, read_json, write_json
from app.services.douyin_collector import (
    FavoriteScrapedCollection,
    FavoriteScrapedVideo,
    FavoriteScrapeSnapshot,
    collector as douyin_collector,
)

logger = logging.getLogger(__name__)

_ZHIHU_HOME = "https://www.zhihu.com"
_ZHIHU_SIGNIN = f"{_ZHIHU_HOME}/signin"
_ZHIHU_PROFILE_PATH = "zhihu_profile.json"
_MAX_COLLECTIONS = 100
_MAX_ITEMS_PER_COLLECTION = 500
_PAGE_SIZE = 50
_LOGIN_TIMEOUT_SECONDS = 10 * 60
_AUTH_COOKIE_NAMES = frozenset({"z_c0"})
_ALLOWED_ZHIHU_HOSTS = frozenset({"zhihu.com", "www.zhihu.com", "zhuanlan.zhihu.com"})
_QR_CANVAS_SELECTOR = "div.Qrcode-container div.Qrcode-img canvas.Qrcode-qrcode"
_QR_IMAGE_SELECTOR = "div.Qrcode-container div.Qrcode-img img"


class ZhihuRequestError(RuntimeError):
    """知乎会话或接口请求失败。"""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class _TextExtractor(HTMLParser):
    """Remove provider UI/advertising nodes without deleting正文关键词。"""

    _SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "iframe"}
    _AD_MARKERS = (
        "advert", "ad-card", "adlink", "sponsor", "promotion", "promoted",
        "banner", "commercial", "mcnlink", "recommendation",
    )
    _BLOCK_TAGS = {"p", "div", "section", "article", "li", "blockquote", "pre", "br"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs_map = {key.lower(): (value or "").lower() for key, value in attrs}
        marker_text = f"{attrs_map.get('id', '')} {attrs_map.get('class', '')}"
        is_ad = any(marker in marker_text for marker in self._AD_MARKERS)
        if self._skip_depth or tag in self._SKIP_TAGS or is_ad:
            self._skip_depth += 1
            return
        if tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if self._skip_depth:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            self._skip_depth -= 1
            return
        if tag.lower() in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        value = html.unescape("".join(self._parts))
        value = re.sub(r"[ \t\r\f\v]+", " ", value)
        value = re.sub(r"\n[ \t]+", "\n", value)
        return re.sub(r"\n{3,}", "\n\n", value).strip()


def _clean_html(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(value)
        parser.close()
        return parser.text()
    except Exception:
        # A malformed provider fragment should not crash the whole collection;
        # returning escaped plain text is still safer than indexing raw HTML.
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(value))).strip()


def _payload_data(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("data", "items", "list", "favlists"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    for key in ("items", "list", "favlists"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _payload_contains_list(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    data = payload.get("data")
    if isinstance(data, list):
        return True
    if isinstance(data, dict) and any(isinstance(data.get(key), list) for key in ("data", "items", "list", "favlists")):
        return True
    return any(isinstance(payload.get(key), list) for key in ("items", "list", "favlists"))


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
    return ""


def _safe_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _provider_url(value: Any) -> str | None:
    """Accept only same-provider paging URLs returned by the API."""
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    if candidate.startswith("/"):
        return f"{_ZHIHU_HOME}{candidate}"
    parsed = urlsplit(candidate)
    if parsed.scheme == "https" and parsed.hostname in _ALLOWED_ZHIHU_HOSTS:
        return candidate
    return None


def _paging_info(payload: dict[str, Any], page_size: int, row_count: int) -> tuple[bool, str | None]:
    paging = payload.get("paging")
    if not isinstance(paging, dict):
        data = payload.get("data")
        paging = data.get("paging") if isinstance(data, dict) else None
    if not isinstance(paging, dict):
        # Without a provider end marker, even a short page is ambiguous: the
        # provider may have silently capped the response.
        return False, None
    is_end = paging.get("is_end")
    if isinstance(is_end, str):
        is_end = is_end.lower() in {"1", "true", "yes"}
    next_url = _provider_url(paging.get("next"))
    if bool(is_end):
        return True, None
    return False, next_url


def _is_public_zhihu_url(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    parsed = urlsplit(value.strip())
    return (
        parsed.scheme == "https"
        and parsed.hostname in _ALLOWED_ZHIHU_HOSTS
        and not parsed.path.startswith("/api/")
    )


def _item_url(item: dict[str, Any], content: dict[str, Any], content_type: str, remote_id: str) -> str:
    url = _first_text(content.get("url"), content.get("share_url"), item.get("url"))
    if _is_public_zhihu_url(url):
        return url
    if content_type in {"article", "zhuanlan"}:
        return f"https://zhuanlan.zhihu.com/p/{quote(remote_id, safe='')}"
    question = content.get("question") if isinstance(content.get("question"), dict) else {}
    question_id = _first_text(question.get("id"))
    if question_id:
        return f"https://www.zhihu.com/question/{quote(question_id, safe='')}/answer/{quote(remote_id, safe='')}"
    if content_type == "answer":
        return f"https://www.zhihu.com/answer/{quote(remote_id, safe='')}"
    return f"https://www.zhihu.com/search?q={quote(remote_id, safe='')}"


class ZhihuCollector:
    """单账号知乎登录、收藏同步和正文提取。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sync_lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._context = None
        self._browser = None
        self._playwright = None
        self._state_path = Path(settings.zhihu_state_path)
        self._profile_path = self._state_path.with_name(_ZHIHU_PROFILE_PATH)
        self._qrcode_image_base64: str | None = None
        self.status = "idle"
        self.message = ""
        self._profile: dict[str, str] = self._load_profile()
        self._check_saved_login()

    def _load_profile(self) -> dict[str, str]:
        try:
            value = read_json(self._profile_path)
            if isinstance(value, dict):
                return {key: str(value.get(key) or "") for key in ("nickname", "avatar_url") if value.get(key)}
        except Exception as exc:
            logger.debug("读取知乎展示资料失败: %s", exc)
        return {}

    def _check_saved_login(self) -> bool:
        try:
            state = read_json(self._state_path)
            cookies = state.get("cookies", []) if isinstance(state, dict) else []
            now = time.time()
            valid = any(
                isinstance(cookie, dict)
                and cookie.get("name") in _AUTH_COOKIE_NAMES
                and (cookie.get("expires", -1) in (-1, 0) or cookie.get("expires", -1) > now)
                for cookie in cookies
            )
            if valid and self.status == "idle":
                self.status = "logged_in"
                self.message = "已登录（凭证有效）"
            return valid
        except Exception as exc:
            logger.debug("检查知乎登录态失败: %s", exc)
            return False

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            if self.status == "idle":
                self._check_saved_login()
            return {
                "status": self.status,
                "message": self.message,
                "nickname": self._profile.get("nickname", ""),
                "avatar_url": self._profile.get("avatar_url", ""),
                "qrcode_image_base64": self._qrcode_image_base64 or "",
            }

    def start_login(self) -> tuple[bool, str]:
        with self._lock:
            if self._check_saved_login():
                self._qrcode_image_base64 = None
                self.status = "logged_in"
                self.message = "已登录（凭证有效）"
                return True, self.message
            if self._thread and self._thread.is_alive():
                return False, "知乎登录窗口已在运行"
            self._cancel.clear()
            self._qrcode_image_base64 = None
            self.status = "pending"
            self.message = "请使用知乎 App 扫码登录"
            self._thread = threading.Thread(target=self._login_worker, name="zhihu-login", daemon=True)
            self._thread.start()
            return True, self.message

    def cancel_login(self) -> tuple[bool, str]:
        self._cancel.set()
        with self._lock:
            self.status = "idle"
            self.message = "已取消知乎登录"
            self._qrcode_image_base64 = None
        return True, "已取消知乎登录"

    def logout(self) -> tuple[bool, str]:
        self.cancel_login()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        delete_json(self._state_path)
        delete_json(self._profile_path)
        with self._lock:
            self._profile = {}
            self.status = "idle"
            self.message = "已退出知乎登录"
        return True, self.message

    def _login_worker(self) -> None:
        context = None
        browser = None
        playwright = None
        try:
            playwright = sync_playwright().start()
            # Keep the whole login flow inside the worker. The frontend receives
            # a screenshot of the QR element, so no browser window is exposed.
            browser = playwright.chromium.launch(**douyin_collector._browser_launch_kwargs(headless=True))
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            page = context.new_page()
            with self._lock:
                self._playwright, self._browser, self._context = playwright, browser, context
            page.goto(_ZHIHU_SIGNIN, wait_until="domcontentloaded", timeout=45_000)
            self._wait_for_qrcode(page)
            deadline = time.monotonic() + _LOGIN_TIMEOUT_SECONDS
            while time.monotonic() < deadline and not self._cancel.is_set():
                if self._page_logged_in(page, context):
                    if self._cancel.is_set():
                        return
                    write_json(self._state_path, context.storage_state())
                    self._profile = self._extract_profile(page)
                    if self._profile:
                        write_json(self._profile_path, self._profile)
                    with self._lock:
                        self.status = "logged_in"
                        self.message = "知乎登录成功"
                    from app.services.worker import worker
                    worker.unblock_platform("zhihu")
                    return
                time.sleep(1)
            if not self._cancel.is_set():
                with self._lock:
                    self.status = "expired"
                    self.message = "知乎登录等待超时，请重新打开登录窗口"
        except Exception as exc:
            if self._cancel.is_set():
                logger.info("知乎登录流程已取消")
                return
            logger.exception("知乎登录失败")
            with self._lock:
                self.status = "failed"
                self.message = f"知乎登录失败: {type(exc).__name__}"
        finally:
            try:
                if context:
                    context.close()
                if browser:
                    browser.close()
                if playwright:
                    playwright.stop()
            except Exception:
                logger.debug("关闭知乎登录浏览器失败", exc_info=True)
            with self._lock:
                self._context = None
                self._browser = None
                self._playwright = None

    @staticmethod
    def _capture_qr_element(element: Any, *, is_canvas: bool) -> str | None:
        """Capture one exact QR renderer only after it is visible and complete."""
        try:
            if not element.is_visible():
                return None
            box = element.bounding_box()
            if not box or box["width"] < 96 or box["height"] < 96:
                return None
            ratio = box["width"] / box["height"]
            if ratio < 0.9 or ratio > 1.1:
                return None
            if is_canvas:
                pixels = element.evaluate("""canvas => {
                    if (!(canvas instanceof HTMLCanvasElement) || !canvas.width || !canvas.height) return null;
                    try {
                        const data = canvas.getContext('2d', { willReadFrequently: true }).getImageData(0, 0, canvas.width, canvas.height).data;
                        let dark = 0, light = 0;
                        for (let i = 0; i < data.length; i += 4) {
                            const value = (data[i] + data[i + 1] + data[i + 2]) / 3;
                            if (value < 64) dark += 1;
                            if (value > 192) light += 1;
                        }
                        return { dark, light, total: canvas.width * canvas.height };
                    } catch (_) { return null; }
                }""")
                if not isinstance(pixels, dict) or not pixels.get("total"):
                    return None
                dark_ratio = pixels.get("dark", 0) / pixels["total"]
                light_ratio = pixels.get("light", 0) / pixels["total"]
                if dark_ratio < 0.01 or light_ratio < 0.1:
                    return None
            elif not element.evaluate("image => image.complete && image.naturalWidth >= 96 && image.naturalHeight >= 96"):
                return None
            return base64.b64encode(element.screenshot(type="png")).decode("ascii")
        except Exception:
            return None

    @classmethod
    def _capture_qrcode(cls, page) -> str | None:
        """Capture Zhihu's actual QR renderer; never select page-wide QR-like content."""
        canvas = page.locator(_QR_CANVAS_SELECTOR)
        if canvas.count() == 1:
            return cls._capture_qr_element(canvas.nth(0), is_canvas=True)
        if canvas.count() > 1:
            return None

        # Zhihu currently renders a canvas. Keep a narrowly scoped fallback for
        # a future image renderer, never for a generic logo or container.
        image = page.locator(_QR_IMAGE_SELECTOR)
        if image.count() != 1:
            return None
        return cls._capture_qr_element(image.nth(0), is_canvas=False)

    def _wait_for_qrcode(self, page) -> None:
        deadline = time.monotonic() + 20
        previous_qrcode: str | None = None
        while time.monotonic() < deadline and not self._cancel.is_set():
            qrcode = self._capture_qrcode(page)
            if qrcode and qrcode == previous_qrcode:
                with self._lock:
                    self._qrcode_image_base64 = qrcode
                    self.message = "请使用知乎 App 扫码登录"
                return
            previous_qrcode = qrcode
            time.sleep(0.3)
        if not self._cancel.is_set():
            raise ZhihuRequestError("未能从知乎登录页获取二维码，请重试")

    @staticmethod
    def _page_logged_in(page, context) -> bool:
        try:
            cookies = context.cookies()
            if not any(cookie.get("name") in _AUTH_COOKIE_NAMES and cookie.get("value") for cookie in cookies):
                return False
            result = page.evaluate("""async () => {
                const response = await fetch('/api/v4/me', {
                    credentials: 'include',
                    headers: {Accept: 'application/json'},
                });
                return {status: response.status, text: await response.text()};
            }""")
            if int(result.get("status", 0)) != 200:
                return False
            payload = json.loads(result.get("text", "{}"))
            value = payload.get("data", payload) if isinstance(payload, dict) else {}
            return isinstance(value, dict) and bool(value.get("id") or value.get("url_token") or value.get("name"))
        except Exception:
            return False

    @staticmethod
    def _extract_profile(page) -> dict[str, str]:
        try:
            value = page.evaluate("""() => ({
                nickname: document.querySelector('[data-za-detail-view-path="UserProfile"]')?.textContent?.trim() || '',
                avatar_url: document.querySelector('img.Avatar, img[class*="Avatar"]')?.src || ''
            })""")
            return {key: str(value.get(key) or "") for key in ("nickname", "avatar_url") if value.get(key)}
        except Exception:
            return {}

    @staticmethod
    def _fetch_json(page, url: str) -> dict[str, Any]:
        result = page.evaluate("""async (target) => {
            const response = await fetch(target, {credentials: 'include', headers: {Accept: 'application/json'}});
            return {status: response.status, text: await response.text()};
        }""", url)
        status = int(result.get("status", 0))
        if status in (401, 403):
            raise ZhihuRequestError(
                f"知乎接口拒绝访问（HTTP {status}），请重新登录或降低同步频率",
                status_code=status,
            )
        if status < 200 or status >= 300:
            raise ZhihuRequestError(f"知乎接口请求失败（HTTP {status}）", status_code=status)
        try:
            payload = json.loads(result.get("text", ""))
        except json.JSONDecodeError as exc:
            raise ZhihuRequestError("知乎接口返回了非 JSON 内容，可能触发了验证页面") from exc
        if not isinstance(payload, dict):
            raise ZhihuRequestError("知乎接口返回格式异常")
        return payload

    def _fetch_paginated(
        self,
        page,
        initial_url: str,
        *,
        page_size: int,
        max_items: int,
        label: str,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Fetch provider pages and report whether the bounded result is complete."""
        rows: list[dict[str, Any]] = []
        next_url: str | None = initial_url
        seen_urls: set[str] = set()
        complete = False

        while next_url and len(rows) < max_items:
            if next_url in seen_urls:
                logger.warning("知乎%s分页链接重复，拒绝声明快照完整", label)
                return rows[:max_items], False
            seen_urls.add(next_url)
            payload = self._fetch_json(page, next_url)
            if not _payload_contains_list(payload):
                logger.warning("知乎%s响应缺少可识别的数据列表，拒绝声明快照完整", label)
                return rows[:max_items], False
            page_rows = _payload_data(payload)
            rows.extend(page_rows)
            page_complete, provider_next = _paging_info(payload, page_size, len(page_rows))
            if page_complete:
                complete = True
                break
            if provider_next is None:
                logger.warning("知乎%s响应未提供下一页链接，拒绝声明快照完整", label)
                break
            next_url = provider_next

        if len(rows) > max_items:
            complete = False
        if not complete and len(rows) >= max_items:
            # The limit may have clipped the provider result. Only `is_end=true`
            # above can prove that the final returned page was the end.
            logger.warning("知乎%s达到本地上限 %d，快照标记为不完整", label, max_items)
        return rows[:max_items], complete

    def _open_sync_page(self):
        state = read_json(self._state_path)
        if not isinstance(state, dict):
            raise ZhihuRequestError("知乎未登录，请先完成登录")
        playwright = browser = context = None
        try:
            playwright = sync_playwright().start()
            browser = playwright.chromium.launch(**douyin_collector._browser_launch_kwargs(headless=True))
            context = browser.new_context(storage_state=state)
            page = context.new_page()
            page.goto(_ZHIHU_HOME, wait_until="domcontentloaded", timeout=45_000)
            if not self._page_logged_in(page, context):
                raise ZhihuRequestError("知乎登录态已失效，请重新登录", status_code=401)
            return playwright, browser, context, page
        except Exception:
            if context:
                try:
                    context.close()
                except Exception:
                    pass
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass
            if playwright:
                try:
                    playwright.stop()
                except Exception:
                    pass
            raise

    def sync_favorites_sync(self) -> FavoriteScrapeSnapshot:
        """在已登录浏览器会话中读取收藏夹与正文。"""
        if not self._sync_lock.acquire(blocking=False):
            raise ZhihuRequestError("知乎同步已在运行，请稍候重试")
        playwright = browser = context = None
        try:
            with self._lock:
                self.status = "syncing"
                self.message = "正在同步知乎收藏夹"
            playwright, browser, context, page = self._open_sync_page()
            collections: list[FavoriteScrapedCollection] = []
            videos: list[FavoriteScrapedVideo] = []
            collection_items: dict[str, list[dict[str, Any]]] = {}
            favlists, collections_complete = self._fetch_paginated(
                page,
                f"{_ZHIHU_HOME}/api/v4/favlists?limit={_PAGE_SIZE}&offset=0",
                page_size=_PAGE_SIZE,
                max_items=_MAX_COLLECTIONS,
                label="收藏夹",
            )
            items_complete = True
            for raw in favlists:
                collection_id = _first_text(raw.get("id"), raw.get("favlist_id"))
                if not collection_id:
                    continue
                title = _first_text(raw.get("title"), raw.get("name"), f"收藏夹 {collection_id}")
                collections.append(FavoriteScrapedCollection(
                    platform_collection_id=collection_id,
                    title=title,
                    video_count=max(_safe_int(raw.get("item_count") or raw.get("items_count") or raw.get("count")), 0),
                ))
                items, collection_complete = self._fetch_paginated(
                    page,
                    f"{_ZHIHU_HOME}/api/v4/favlists/{quote(collection_id, safe='')}/items?limit={_PAGE_SIZE}&offset=0",
                    page_size=_PAGE_SIZE,
                    max_items=_MAX_ITEMS_PER_COLLECTION,
                    label=f"收藏夹 {collection_id} 内容",
                )
                collection_items[collection_id] = items
                items_complete = items_complete and collection_complete

            by_key: dict[str, FavoriteScrapedVideo] = {}
            for collection_id, raw_items in collection_items.items():
                for raw in raw_items:
                    parsed = self._parse_item(raw)
                    if parsed is None:
                        continue
                    parsed.collection_ids.add(collection_id)
                    key = parsed.platform_item_id
                    current = by_key.get(key)
                    if current is None:
                        by_key[key] = parsed
                    else:
                        current.collection_ids.update(parsed.collection_ids)
            for item in by_key.values():
                if len(item.text_content.strip()) < 10:
                    self._enrich_item_content(page, item)
            videos = list(by_key.values())
            snapshot = FavoriteScrapeSnapshot(
                collections=collections,
                videos=videos,
                platform="zhihu",
                is_complete=collections_complete and items_complete,
            )
            with self._lock:
                self.status = "logged_in"
                suffix = "（结果不完整，未清理旧收藏）" if not snapshot.is_complete else ""
                self.message = f"知乎同步完成：{len(collections)} 个收藏夹，{len(videos)} 条内容{suffix}"
            return snapshot
        except ZhihuRequestError as exc:
            with self._lock:
                self.status = "expired" if exc.status_code in (401, 403) else "failed"
                self.message = str(exc)
            raise
        except Exception as exc:
            with self._lock:
                self.status = "failed"
                self.message = f"知乎同步失败: {type(exc).__name__}"
            raise
        finally:
            try:
                if context:
                    context.close()
                if browser:
                    browser.close()
                if playwright:
                    playwright.stop()
            finally:
                if self.status == "syncing":
                    self.status = "logged_in"
                self._sync_lock.release()

    def _parse_item(self, raw: dict[str, Any]) -> FavoriteScrapedVideo | None:
        content = raw.get("content") if isinstance(raw.get("content"), dict) else raw
        content_type = _first_text(content.get("type"), content.get("content_type"), raw.get("type")).lower()
        remote_id = _first_text(content.get("id"), raw.get("id"))
        if not remote_id:
            url = _first_text(content.get("url"), raw.get("url"))
            remote_id = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32] if url else ""
        if not remote_id:
            return None
        if content_type in {"zhuanlan", "post"}:
            content_type = "article"
        prefix = "article" if content_type == "article" else "answer" if content_type == "answer" else "content"
        remote_item_id = f"zhihu:{prefix}:{remote_id}"[:64]
        question = content.get("question") if isinstance(content.get("question"), dict) else {}
        title = _first_text(content.get("title"), question.get("title"), raw.get("title"), content.get("name"), remote_item_id)
        author = content.get("author") if isinstance(content.get("author"), dict) else {}
        body = _clean_html(_first_text(content.get("content"), content.get("excerpt"), content.get("description"), raw.get("excerpt")))
        url = _item_url(raw, content, content_type, remote_id)
        return FavoriteScrapedVideo(
            platform_item_id=remote_item_id,
            url=url,
            title=title,
            author=_first_text(author.get("name"), author.get("url_token")),
            duration=0,
            collection_ids=set(),
            text_content=body,
        )

    def _enrich_item_content(self, page, item: FavoriteScrapedVideo) -> None:
        """Fetch a full answer/article only when the collection payload is thin."""
        parts = item.platform_item_id.split(":", 2)
        if len(parts) != 3:
            return
        kind, remote_id = parts[1], parts[2]
        endpoint = "answers" if kind == "answer" else "articles" if kind == "article" else ""
        if not endpoint:
            return
        try:
            detail = self._fetch_json(page, f"{_ZHIHU_HOME}/api/v4/{endpoint}/{quote(remote_id, safe='')}")
            rows = _payload_data(detail)
            value = detail.get("data") if isinstance(detail.get("data"), dict) else (rows[0] if rows else detail)
            if isinstance(value, dict):
                text = _clean_html(_first_text(value.get("content"), value.get("excerpt"), value.get("description")))
                if len(text) >= len(item.text_content):
                    item.text_content = text
                question = value.get("question") if isinstance(value.get("question"), dict) else {}
                item.title = _first_text(value.get("title"), question.get("title"), item.title)
                author = value.get("author") if isinstance(value.get("author"), dict) else {}
                item.author = _first_text(author.get("name"), item.author)
                item.url = _item_url(value, value, kind, remote_id)
        except ZhihuRequestError as exc:
            if exc.status_code in (401, 403):
                raise
            logger.warning("知乎内容详情不可用 [%s]: %s", item.platform_item_id, exc)
        except Exception as exc:
            logger.warning("知乎内容详情获取失败 [%s]: %s", item.platform_item_id, type(exc).__name__)


zhihu_collector = ZhihuCollector()
