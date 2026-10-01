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
import io
import asyncio
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from playwright.sync_api import sync_playwright
from PIL import Image

from app.core.config import settings
from app.core.external_urls import safe_platform_image_url
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
        self._previous_qrcode: str | None = None
        self.status = "idle"
        self.message = ""
        self._profile: dict[str, str] = self._load_profile()
        self._check_saved_login()

    def _load_profile(self) -> dict[str, str]:
        try:
            value = read_json(self._profile_path)
            if isinstance(value, dict):
                return self._safe_profile(value)
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
            self._previous_qrcode = None
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

    async def wait_for_login_retirement(self, timeout: float = 3.0) -> bool:
        """Wait off the API loop for an expired browser worker to release it."""
        status = self.status
        thread = self._thread
        if status not in ("expired", "failed") or not thread or not thread.is_alive():
            return True
        await asyncio.to_thread(thread.join, timeout)
        return not thread.is_alive() and self.status == status

    def logout(self) -> tuple[bool, str]:
        self.cancel_login()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        with self._lock:
            delete_json(self._state_path)
            delete_json(self._profile_path)
            self._profile = {}
            self.status = "idle"
            self.message = "已退出知乎登录"
        return True, self.message

    def _invalidate_saved_login(self, message: str) -> None:
        """Remove a provider-rejected session so the next UI read can re-login.

        A locally unexpired ``z_c0`` cookie is not proof that Zhihu still
        accepts the session.  Keeping such a state after a failed live check
        makes the frontend disable the Zhihu login tab indefinitely.
        """
        with self._lock:
            delete_json(self._state_path)
            delete_json(self._profile_path)
            self._profile = {}
            self._qrcode_image_base64 = None
            self.status = "idle"
            self.message = message

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
                    # The verified /me payload is authoritative. DOM fallback
                    # may fill missing fields, never replace its account name.
                    self._remember_profile({
                        key: value for key, value in self._extract_profile(page).items()
                        if not self._profile.get(key)
                    })
                    with self._lock:
                        self._qrcode_image_base64 = None
                        self.status = "logged_in"
                        self.message = "知乎登录成功"
                    from app.services.worker import worker
                    worker.unblock_platform("zhihu")
                    return
                self._poll_qrcode(page)
                # Dispatch browser events while waiting so provider-side QR
                # rotation continues in this same browser session.
                page.wait_for_timeout(1000)
            if not self._cancel.is_set():
                with self._lock:
                    self.status = "expired"
                    self.message = "知乎登录等待超时，请重新打开登录窗口"
                    self._qrcode_image_base64 = None
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
        """Capture one exact, visible QR renderer.

        Zhihu draws its QR canvas with a cross-origin image source.  Calling
        ``getImageData`` on that canvas raises ``SecurityError`` even though it
        is fully rendered and Playwright can screenshot it.  Do not inspect
        canvas pixels here: the exact selector, geometry checks, and the
        two-identical-screenshots gate in :meth:`_wait_for_qrcode` are the
        reliable completeness proof that does not break cross-origin canvases.
        """
        try:
            if not element.is_visible():
                return None
            box = element.bounding_box()
            if not box or not (96 <= box["width"] <= 1024 and 96 <= box["height"] <= 1024):
                return None
            ratio = box["width"] / box["height"]
            if ratio < 0.9 or ratio > 1.1:
                return None
            if not is_canvas and not element.evaluate("image => image.complete && image.naturalWidth >= 96 && image.naturalHeight >= 96"):
                return None
            screenshot = element.screenshot(type="png")
            if len(screenshot) > 4 * 1024 * 1024:
                return None
            # Inspect the screenshot rather than reading a potentially tainted
            # canvas. Two stable screenshots can also be an entirely blank
            # renderer: require meaningful dark and light pixel populations.
            with Image.open(io.BytesIO(screenshot)) as image:
                if image.format != "PNG" or not (96 <= image.width <= 2048 and 96 <= image.height <= 2048):
                    return None
                histogram = image.convert("L").histogram()
                minimum = image.width * image.height * 0.02
                if sum(histogram[:81]) < minimum or sum(histogram[175:]) < minimum:
                    return None
            return base64.b64encode(screenshot).decode("ascii")
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
                    if self._cancel.is_set():
                        return
                    self._qrcode_image_base64 = qrcode
                    self._previous_qrcode = qrcode
                    self.message = "请使用知乎 App 扫码登录"
                return
            previous_qrcode = qrcode
            time.sleep(0.3)
        if not self._cancel.is_set():
            raise ZhihuRequestError("未能从知乎登录页获取二维码，请重试")

    @staticmethod
    def _qrcode_expired(page) -> bool:
        # Read only the visible QR UI. Text elsewhere on the sign-in page
        # must not trigger a reload or interrupt phone confirmation.
        return bool(page.evaluate(r"""() => {
            const root = document.querySelector('div.Qrcode-container');
            if (!root || !root.getClientRects().length) return false;
            if (getComputedStyle(root).visibility !== 'visible' ||
                Number(getComputedStyle(root).opacity) === 0) return false;
            return /二维码.{0,8}(?:过期|失效)|(?:过期|失效).{0,8}二维码|点击(?:此处)?刷新|QR\s*code.{0,12}expired/i.test(root.innerText);
        }"""))

    def _poll_qrcode(self, page) -> None:
        if self._cancel.is_set():
            return
        if self._qrcode_expired(page):
            with self._lock:
                self._qrcode_image_base64 = None
                self._previous_qrcode = None
                self.message = "二维码已过期，正在获取新的登录二维码"
            # Reload the same sign-in page/context instead of leaving an
            # expired canvas visible or creating a second browser worker.
            page.reload(wait_until="domcontentloaded", timeout=20_000)
            self._wait_for_qrcode(page)
            return
        qrcode = self._capture_qrcode(page)
        with self._lock:
            if self._cancel.is_set():
                return
            if qrcode and qrcode == self._previous_qrcode:
                self._qrcode_image_base64 = qrcode
                self.message = "请使用知乎 App 扫码登录"
            elif qrcode != self._qrcode_image_base64:
                # A changed/incomplete renderer cannot be scanned until the
                # second identical sample confirms that it has finished.
                self._qrcode_image_base64 = None
                self.message = "正在获取新的登录二维码，请稍等"
            self._previous_qrcode = qrcode

    def _page_logged_in(self, page, context) -> bool:
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
            logged_in = isinstance(value, dict) and bool(value.get("id") or value.get("url_token") or value.get("name"))
            if logged_in:
                # The current-account check already fetches these fields. Do
                # not scrape another user's answer card or issue another API
                # request merely to populate the account display.
                profile = {"nickname": value.get("name"), "avatar_url": value.get("avatar_url")}
                if not profile["avatar_url"] and isinstance(value.get("avatar_url_template"), str):
                    profile["avatar_url"] = value["avatar_url_template"].replace("{size}", "xl")
                self._remember_profile(profile)
            return logged_in
        except Exception:
            return False

    @staticmethod
    def _safe_profile(value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        nickname = value.get("nickname")
        nickname = nickname.strip()[:128] if isinstance(nickname, str) else ""
        avatar = safe_platform_image_url("zhihu", value.get("avatar_url"))
        return {key: item for key, item in (("nickname", nickname), ("avatar_url", avatar)) if item}

    def _remember_profile(self, value: Any) -> None:
        profile = self._safe_profile(value)
        if not profile:
            return
        with self._lock:
            if self._cancel.is_set():
                return
            merged = {**self._profile, **profile}
            if merged == self._profile:
                return
            # Display-cache persistence must not change authentication success.
            try:
                write_json(self._profile_path, merged)
            except Exception:
                logger.debug("保存知乎展示资料失败", exc_info=True)
            self._profile = merged

    @staticmethod
    def _extract_profile(page) -> dict[str, str]:
        try:
            value = page.evaluate("""() => {
                const visible = node => !!node && node.getClientRects().length > 0 &&
                    getComputedStyle(node).visibility !== 'hidden';
                // The header account button belongs to the logged-in user;
                // page-wide Avatar/UserLink selectors also match feed authors.
                const header = document.querySelector('.AppHeader');
                const avatar = header?.querySelector('img.AppHeader-profileAvatar');
                if (!visible(avatar)) return {};
                const name = avatar.alt?.trim() || '';
                return {
                    nickname: ['头像', '我的头像', '用户头像', 'Avatar', 'avatar'].includes(name) ? '' : name,
                    avatar_url: avatar.currentSrc || avatar.src || '',
                };
            }""")
            return ZhihuCollector._safe_profile(value)
        except Exception:
            return {}

    @staticmethod
    def _fetch_json(page, url: str) -> dict[str, Any]:
        result = page.evaluate("""async (target) => {
            const response = await fetch(target, {credentials: 'include', headers: {Accept: 'application/json'}});
            return {
                status: response.status,
                contentType: response.headers.get('content-type') || '',
                text: await response.text(),
            };
        }""", url)
        status = int(result.get("status", 0))
        if status in (401, 403):
            raise ZhihuRequestError(
                f"知乎接口拒绝访问（HTTP {status}），请重新登录或降低同步频率",
                status_code=status,
            )
        if status < 200 or status >= 300:
            raise ZhihuRequestError(f"知乎接口请求失败（HTTP {status}）", status_code=status)
        if "json" not in str(result.get("contentType") or "").lower():
            raise ZhihuRequestError("知乎接口没有返回 JSON；收藏夹将使用已登录网页读取")
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

    @staticmethod
    def _normalise_page_collections(rows: Any) -> list[dict[str, str]]:
        """Validate the small DOM projection used for the signed-favlist fallback."""
        if not isinstance(rows, list):
            return []
        seen: set[str] = set()
        collections: list[dict[str, str]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            collection_id = _first_text(row.get("id"))
            if not collection_id.isdigit() or collection_id in seen:
                continue
            seen.add(collection_id)
            title = _first_text(row.get("title"), f"收藏夹 {collection_id}")[:300]
            collections.append({"id": collection_id, "title": title})
        return collections

    @staticmethod
    def _normalise_page_items(rows: Any) -> list[dict[str, Any]]:
        """Turn trusted same-origin card projections into the existing item shape."""
        if not isinstance(rows, list):
            return []
        items: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            kind = _first_text(row.get("type")).lower()
            remote_id = _first_text(row.get("id"))
            url = _first_text(row.get("url"))
            if kind not in {"answer", "article"} or not remote_id or not _is_public_zhihu_url(url):
                continue
            key = (kind, remote_id)
            if key in seen:
                continue
            seen.add(key)
            items.append({
                "content": {
                    "type": kind,
                    "id": remote_id,
                    "url": url,
                    "title": _first_text(row.get("title"), f"知乎{kind} {remote_id}")[:500],
                    "author": {"name": _first_text(row.get("author"))[:200]},
                    "excerpt": _first_text(row.get("excerpt"))[:20_000],
                }
            })
        return items

    def _read_collections_from_page(self, page) -> tuple[list[dict[str, str]], dict[str, list[dict[str, Any]]]]:
        """Read the signed Zhihu collection UI through the already logged-in page.

        Zhihu's collection REST endpoints require an ephemeral browser signature.
        Calling them with a hand-written ``fetch`` can return HTTP 200 plus an
        HTML/null body or HTTP 403.  The normal web pages already run in the
        authenticated browser context and server-render the visible cards, so
        use a deliberately narrow DOM projection instead.  This is always an
        incomplete snapshot: pagination is not guessed, and old local rows are
        consequently never removed from a partial provider view.
        """
        account = self._fetch_json(page, f"{_ZHIHU_HOME}/api/v4/me")
        url_token = _first_text(account.get("url_token"))
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", url_token):
            raise ZhihuRequestError("知乎登录态缺少账号标识，请重新登录", status_code=401)

        page.goto(
            f"{_ZHIHU_HOME}/people/{quote(url_token, safe='')}/collections",
            wait_until="domcontentloaded",
            timeout=45_000,
        )
        page.wait_for_selector("main", state="attached", timeout=15_000)
        try:
            page.wait_for_selector('main a[href*="/collection/"]', state="attached", timeout=10_000)
        except Exception:
            is_explicitly_empty = page.evaluate(
                "() => document.body.innerText.includes('还没有收藏夹')"
            )
            if not is_explicitly_empty:
                raise ZhihuRequestError("知乎收藏夹页面未加载完成，请稍后重试")
        raw_collections = page.evaluate("""() => {
            const rows = [];
            const seen = new Set();
            for (const link of document.querySelectorAll('main a[href*="/collection/"]')) {
                const match = (link.getAttribute('href') || '').match(/^\\/collection\\/(\\d+)(?:[/?#].*)?$/);
                if (!match || seen.has(match[1])) continue;
                seen.add(match[1]);
                const heading = link.querySelector('h1, h2, h3, [class*="Title"]');
                const title = (heading?.textContent || link.textContent || '').trim().split(/\\n+/)[0];
                rows.push({id: match[1], title});
            }
            return rows;
        }""")
        collections = self._normalise_page_collections(raw_collections)
        collection_items: dict[str, list[dict[str, Any]]] = {}
        for collection in collections:
            collection_id = collection["id"]
            page.goto(
                f"{_ZHIHU_HOME}/collection/{quote(collection_id, safe='')}",
                wait_until="domcontentloaded",
                timeout=45_000,
            )
            page.wait_for_selector("main", state="attached", timeout=15_000)
            try:
                page.wait_for_selector("main .ContentItem", state="attached", timeout=8_000)
            except Exception:
                # An empty collection is valid.  Treat an uncertain page as
                # partial rather than inventing a destructive empty result.
                logger.info("知乎收藏夹 %s 当前未渲染内容卡片", collection_id)
            raw_items = page.evaluate("""() => {
                const rows = [];
                for (const card of document.querySelectorAll('main .ContentItem')) {
                    const links = [...card.querySelectorAll('a[href]')]
                        .map(link => new URL(link.getAttribute('href'), location.origin).href);
                    const answer = links.find(url => /\\/question\\/\\d+\\/answer\\/(\\d+)(?:[/?#]|$)/.test(url));
                    const article = links.find(url => /(?:zhuanlan\\.)?zhihu\\.com\\/p\\/(\\d+)(?:[/?#]|$)/.test(url));
                    const url = answer || article;
                    const match = url?.match(answer ? /\\/answer\\/(\\d+)(?:[/?#]|$)/ : /\\/p\\/(\\d+)(?:[/?#]|$)/);
                    if (!url || !match) continue;
                    const titleNode = card.querySelector('h1, h2, h3, [class*="Title"]');
                    const authorNode = card.querySelector('.AuthorInfo-name, .UserLink-link');
                    const bodyNode = card.querySelector('.RichContent-inner, [class*="RichContent"]');
                    rows.push({
                        type: answer ? 'answer' : 'article',
                        id: match[1],
                        url,
                        title: (titleNode?.textContent || '').trim(),
                        author: (authorNode?.textContent || '').trim(),
                        excerpt: (bodyNode?.textContent || card.textContent || '').trim(),
                    });
                }
                return rows;
            }""")
            collection_items[collection_id] = self._normalise_page_items(raw_items)
        return collections, collection_items

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
                self._invalidate_saved_login("知乎登录态已失效，请重新登录")
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
            favlists, collection_items = self._read_collections_from_page(page)
            # The UI can be lazily paginated.  Never treat the first rendered
            # page as exhaustive, so snapshot persistence cannot deactivate
            # collections/items that were simply below the fold.
            collections_complete = False
            items_complete = False
            for raw in favlists:
                collection_id = _first_text(raw.get("id"))
                if not collection_id:
                    continue
                title = _first_text(raw.get("title"), f"收藏夹 {collection_id}")
                collections.append(FavoriteScrapedCollection(
                    platform_collection_id=collection_id,
                    title=title,
                    video_count=len(collection_items.get(collection_id, [])),
                ))

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
