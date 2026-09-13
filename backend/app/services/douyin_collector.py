"""
抖音采集服务模块

核心思路：登录后不关浏览器，在同一个持久化上下文中立即抓取数据，
抓完再关。避免 Cookie/Storage 跨 session 丢失的问题。

登录 → 检测到 Cookie → 立即调 Webpack API 抓收藏夹 → 存快照 → 关浏览器
同步 → 直接返回已缓存的快照（不重新打开浏览器）
"""
from __future__ import annotations

import asyncio
import base64
import logging
import shutil
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright

from app.core.config import settings
from app.core.secure_storage import delete_json, read_json, write_json

logger = logging.getLogger(__name__)


#: Playwright renamed the Windows Chromium payload directory from ``chrome-win``
#: to ``chrome-win64``; current builds ship the latter.  Matching only the old
#: name silently returns None and falls back to a *system* Chrome/Edge channel,
#: which looks fine on a developer machine and breaks on a clean one where the
#: project-local Chromium is the only browser installed.  Keep both patterns,
#: and keep this list in sync with ``douyin_media_resolver._browser_launch_kwargs``.
_CHROMIUM_GLOBS = ("chromium-*/chrome-win/chrome.exe", "chromium-*/chrome-win64/chrome.exe")


def _find_project_chromium_executable() -> Optional[Path]:
    """在 Playwright 浏览器目录中查找 Chromium"""
    base = Path(settings.playwright_browsers_path)
    if not base.exists():
        return None
    for pattern in _CHROMIUM_GLOBS:
        for candidate in sorted(base.glob(pattern), reverse=True):
            if candidate.exists():
                return candidate
    return None


def _find_user_data_dir() -> Path:
    """跨工作目录查找 Playwright 用户持久化数据目录"""
    candidates = [
        Path(settings.playwright_user_data_dir),
        Path(__file__).resolve().parent.parent / "storage" / "playwright_user_data",
        Path.cwd() / "backend" / "app" / "storage" / "playwright_user_data",
    ]
    for p in candidates:
        if p.exists():
            return p
    target = candidates[1]
    target.mkdir(parents=True, exist_ok=True)
    return target


@dataclass
class FavoriteScrapedCollection:
    platform_collection_id: str
    title: str
    video_count: int
    cover_url: Optional[str] = None


@dataclass
class FavoriteScrapedVideo:
    platform_item_id: str
    url: str
    title: str
    author: str
    duration: Optional[int] = None
    collection_ids: set[str] = field(default_factory=set)
    # Provider-native independent parts, e.g. Bilibili pages/CIDs.
    parts: list[dict] = field(default_factory=list)
    # True when this sync actually re-fetched provider part/page data this
    # round (BUG-10/NET-04) -- vs. reusing cached ContentPart rows because
    # the enrichment TTL hadn't expired. Only the former should advance
    # ContentItem.last_enriched_at when persisted.
    freshly_enriched: bool = False


@dataclass
class FavoriteScrapeSnapshot:
    collections: list[FavoriteScrapedCollection] = field(default_factory=list)
    videos: list[FavoriteScrapedVideo] = field(default_factory=list)
    platform: str = "douyin"
    is_complete: bool = True
    invalid_count: int = 0


class DouyinCollector:
    """抖音数据采集器 — 登录 + 抓取一体化"""

    def __init__(self) -> None:
        self.status: str = "idle"
        self.message: str = ""
        self._login_task: Optional[asyncio.Task] = None
        self._lock = threading.Lock()
        self._logout_requested = threading.Event()
        self._profile_cleanup_pending = threading.Event()
        self._active_context = None
        self._active_playwright = None
        self._profile: dict[str, str] = {}
        self.user_data_dir: Path = _find_user_data_dir()
        self.storage_state_path: Path = self.user_data_dir / "state.json"
        self._snapshot: Optional[FavoriteScrapeSnapshot] = None
        self._qr_image_base64: Optional[str] = None
        self._qr_ready_event = threading.Event()
        self._qr_error: Optional[str] = None
        self._headless_mode: bool = True
        self._cdp_session = None
        self._window_id: Optional[int] = None
        self._active_page = None
        # 启动时自动检查本地已有的持久化凭据
        self._check_saved_login()

    def _check_saved_login(self) -> bool:
        """检查本地保存的 storage_state 是否包含有效登录凭证"""
        try:
            data = read_json(self.storage_state_path)
            if data is None:
                return False
            cookies = data.get("cookies", []) if isinstance(data, dict) else []
            now = time.time()
            has_valid_cookie = False
            for c in cookies:
                if not isinstance(c, dict):
                    continue
                name = str(c.get("name") or "")
                if name in {"sessionid", "sid_guard", "sid_tt"}:
                    expires = c.get("expires", -1)
                    if expires == -1 or expires > now:
                        has_valid_cookie = True
                        break
            if has_valid_cookie:
                if self.status == "idle":
                    self.status = "logged_in"
                    self.message = "已登录（凭证有效）"
                return True
        except Exception as err:
            logger.warning("检查本地登录凭据异常: %s", err)
        return False

    def get_status(self) -> tuple[str, str]:
        """查询当前状态（若处于 idle 则先检测本地凭据）"""
        if self.status == "idle":
            self._check_saved_login()
        return self.status, self.message

    def get_profile(self) -> dict[str, str]:
        """Return the best-effort display profile captured during login."""
        return dict(self._profile)

    @staticmethod
    def _extract_profile(page) -> dict[str, str]:
        """Best-effort UI extraction; profile rendering must never gate login."""
        try:
            return page.evaluate("""() => {
                const avatar = [
                  '[data-e2e="user-avatar"] img',
                  '[data-e2e="user-info"] img',
                  'img[src*="douyinpic.com"]',
                ].map(selector => document.querySelector(selector)?.src || '').find(Boolean) || '';
                const nickname = [
                  '[data-e2e="user-name"]',
                  '[data-e2e="user-info"] [title]',
                ].map(selector => document.querySelector(selector)?.textContent?.trim() || '').find(Boolean) || '';
                return { avatar_url: avatar, nickname };
            }""") or {}
        except Exception as exc:
            logger.info("抖音账号展示资料暂不可用: %s", exc)
            return {}

    # ------------------------------------------------------------------
    # 浏览器
    # ------------------------------------------------------------------

    def _browser_launch_kwargs(self, headless: bool = True) -> dict:
        """构建浏览器启动参数，默认无头纯后台静默运行"""
        args = [
            "--disable-blink-features=AutomationControlled",
        ]
        executable = _find_project_chromium_executable()
        if executable:
            return {"headless": headless, "executable_path": str(executable), "args": args}
        channel = settings.playwright_browser_channel.strip()
        if channel and channel != "chromium":
            return {"headless": headless, "channel": channel, "args": args}
        if sys.platform == "win32":
            return {"headless": headless, "channel": "msedge", "args": args}
        return {"headless": headless, "args": args}

    @staticmethod
    def _extract_qrcode_from_page(page) -> Optional[str]:
        """
        从抖音登录页精确提取二维码（严格等待二维码图片或点阵渲染完成，坚决不返回未就绪的 loading 占位图）
        """
        # 1. 核心链路：等待 #animate_qrcode_container 内的 Base64 img 渲染就绪
        try:
            # 抖音前端在二维码尚未生成时，#animate_qrcode_container 内部仅含居中抖音 LOGO 的 loading 占位图；
            # 当二维码生成就绪后，会在容器内插入带有 data:image 的 img 元素。
            qr_img = page.locator('#animate_qrcode_container img[src^="data:image"], #animate_qrcode_container img').first
            qr_img.wait_for(state="visible", timeout=25_000)

            # 优先截取带外框与中心 LOGO 的完整二维码卡片（实测 12KB 左右，与抖音官方视觉完全一致）
            c_loc = page.locator('#animate_qrcode_container').first
            if c_loc.count() > 0:
                shot_bytes = c_loc.screenshot()
                # 真实的二维码容器截图在 10KB~15KB，而未就绪的 loading 占位图仅 2.1KB
                if len(shot_bytes) > 5000:
                    logger.info("已成功截取完整抖音二维码卡片 (bytes=%d)", len(shot_bytes))
                    return f"data:image/png;base64,{base64.b64encode(shot_bytes).decode('ascii')}"

            # 若截图因时序等原因未达阈值，直接提取 img 的 base64 src（纯净黑白二维码矩阵）
            src = qr_img.get_attribute("src") or ""
            if src.startswith("data:image"):
                logger.info("从 img[src] 提取到原始 Base64 二维码 (len=%d)", len(src))
                return src
        except Exception as err:
            logger.warning("等待 #animate_qrcode_container 二维码就绪超时或异常: %s", err)

        # 2. 备选方案：轮询页面中任何正方形二维码元素
        try:
            for _ in range(10):
                for el in page.locator('img[src^="data:image"], canvas').all():
                    box = el.bounding_box()
                    if box and 120 <= box["width"] <= 250 and 120 <= box["height"] <= 250:
                        ratio = box["width"] / max(box["height"], 1)
                        if 0.8 <= ratio <= 1.25:
                            src = el.get_attribute("src") or ""
                            if src.startswith("data:image"):
                                logger.info("备选方案提取到正方形二维码图片: %s", box)
                                return src
                            shot_bytes = el.screenshot()
                            if len(shot_bytes) > 5000:
                                logger.info("备选方案完成正方形元素截图: %s", box)
                                return f"data:image/png;base64,{base64.b64encode(shot_bytes).decode('ascii')}"
                time.sleep(0.5)
        except Exception as fallback_err:
            logger.warning("备选二维码提取也失败: %s", fallback_err)

        return None

    # ------------------------------------------------------------------
    # 登录 + 同步抓取（在同一个浏览器会话中完成）
    # ------------------------------------------------------------------

    def start_login(self, headless: bool = True) -> tuple[bool, str]:
        """启动扫码登录（默认无头纯后台，桌面无弹窗，任务栏无图标）"""
        if self._profile_cleanup_pending.is_set():
            return False, "正在安全清理上一次登录会话，请稍候重试"
        self._headless_mode = headless
        # 幂等保护：若当前正在等待扫码、正在同步或后台锁已被占用，直接返回进行中，绝不重复打开新浏览器窗口
        if (self.status in ("pending", "syncing") and self._login_task and not self._login_task.done()) or self._lock.locked():
            return True, self.message or "登录正在进行中，请扫码操作"
        self._logout_requested.clear()
        self.status = "pending"
        self.message = "请在登录弹窗中扫描二维码"
        self._snapshot = None
        self._login_task = asyncio.ensure_future(self._login_flow())
        return True, self.message

    def get_qrcode(self) -> Optional[str]:
        """获取当前准备好的抖音二维码 Base64 图片"""
        return self._qr_image_base64

    async def wait_for_qrcode(self, timeout: float = 30.0) -> Optional[str]:
        """等待二维码就绪（如果在未启动状态则先触发启动）"""
        if self.status not in ("pending", "syncing") and not self._lock.locked():
            self.start_login()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._qr_ready_event.wait, timeout)
        return self._qr_image_base64

    def show_browser_window(self) -> bool:
        """用户请求在独立窗口打开时，切换至可见模式重新启动，供滑块拼图或手动操作"""
        if not self._headless_mode and self._cdp_session and self._window_id is not None:
            try:
                self._cdp_session.send("Browser.setWindowBounds", {
                    "windowId": self._window_id,
                    "bounds": {"windowState": "normal"}
                })
                logger.info("已通过 CDP 恢复抖音登录窗口至正常显示状态")
                return True
            except Exception as exc:
                logger.warning("CDP 恢复窗口异常: %s", exc)
        try:
            logger.info("用户请求展开独立窗口，以可见模式重启登录会话...")
            self.cancel_login()
            time.sleep(0.3)
            self._headless_mode = False
            self.start_login(headless=False)
            return True
        except Exception as exc:
            logger.warning("启动可见窗口异常: %s", exc)
            return False

    def _save_storage_state(self, context) -> None:
        """Persist Playwright cookies with DPAPI instead of plaintext JSON."""
        if self._logout_requested.is_set():
            return
        write_json(self.storage_state_path, context.storage_state())

    def refresh_qrcode(self) -> Optional[str]:
        """刷新当前抖音二维码"""
        if not self._active_page or self.status not in ("pending", "syncing"):
            return None
        try:
            refresh_btn = self._active_page.locator(
                '#animate_qrcode_container button, #douyin_login_comp_scan_code button, [class*="refresh"], [class*="reload"]'
            ).first
            if refresh_btn.is_visible(timeout=1000):
                refresh_btn.click()
                time.sleep(1.0)
            else:
                self._active_page.reload(wait_until="domcontentloaded", timeout=15000)
            time.sleep(1.5)
            qr_b64 = self._extract_qrcode_from_page(self._active_page)
            if qr_b64:
                self._qr_image_base64 = qr_b64
            return self._qr_image_base64
        except Exception as exc:
            logger.warning("刷新抖音二维码异常: %s", exc)
            return None

    def cancel_login(self) -> tuple[bool, str]:
        """取消未完成的扫码登录，安全关闭浏览器实例并回收资源"""
        # 如果当前已经扫码成功（处于 syncing 同步中或 logged_in 登录完成），绝不能作废或打断有效登录
        if self.status in ("syncing", "logged_in"):
            return True, "已成功扫码登录，后台持续同步中"

        if self._login_task and not self._login_task.done():
            self._login_task.cancel()
            self._login_task = None
        # The worker owns this sync Playwright object. Signal it instead of
        # closing it from the FastAPI event-loop thread.
        self._logout_requested.set()
        self._qr_ready_event.set()
        self._qr_image_base64 = None
        self._active_page = None
        self._cdp_session = None
        self._window_id = None
        self._active_context = None
        if self._check_saved_login():
            self.status = "logged_in"
            self.message = "已取消本次重新登录，保持原有登录态"
        else:
            self.status = "idle"
            self.message = "已取消登录"
        return True, self.message

    async def _login_flow(self) -> None:
        await asyncio.to_thread(self._login_and_fetch_sync)

    def _login_and_fetch_sync(self) -> None:
        """
        在同一个持久化浏览器上下文中完成：登录检测 → 收藏夹抓取 → 本地数据库持久化

        关键防竞争：利用 threading.Lock 保证同一时刻只有一个浏览器运行
        """
        acquired = self._lock.acquire(blocking=False)
        if not acquired:
            logger.warning("已有登录/抓取线程在运行，跳过本次触发")
            return
        self._qr_image_base64 = None
        self._qr_ready_event.clear()
        self._qr_error = None
        try:
            with sync_playwright() as p:
                self._active_playwright = p
                kwargs = self._browser_launch_kwargs(headless=self._headless_mode)
                logger.info("启动登录浏览器: %s", kwargs)
                context = None
                try:
                    context = p.chromium.launch_persistent_context(
                        user_data_dir=str(self.user_data_dir),
                        **kwargs,
                    )
                    self._active_context = context
                    page = context.pages[0] if context.pages else context.new_page()
                    self._active_page = page

                    try:
                        cdp = context.new_cdp_session(page)
                        self._cdp_session = cdp
                        win = cdp.send("Browser.getWindowForTarget")
                        self._window_id = win.get("windowId")
                    except Exception as cdp_err:
                        logger.debug("CDP 初始化异常: %s", cdp_err)

                    # 1. 导航用户中心以触发登录弹窗并提供网络抖动重试
                    nav_target = f"{settings.douyin_home_url.rstrip('/')}/user/self"
                    try:
                        page.goto(nav_target, timeout=60_000, wait_until="domcontentloaded")
                    except Exception as nav_err:
                        logger.warning("用户页初次加载异常，尝试重试: %s", nav_err)
                        time.sleep(1.5)
                        page.goto(nav_target, timeout=60_000, wait_until="domcontentloaded")

                    logger.info("已打开抖音登录页，正在提取登录二维码...")

                    # 尝试定位并截取二维码（精准定位 #animate_qrcode_container，杜绝顶部横幅误截）
                    try:
                        qr_b64 = self._extract_qrcode_from_page(page)
                        if qr_b64:
                            self._qr_image_base64 = qr_b64
                            logger.info("抖音登录二维码获取成功，已保存 Base64 图片 (len=%d)", len(self._qr_image_base64))
                        else:
                            raise RuntimeError("未在页面中找到符合规格的二维码元素")
                    except Exception as qr_err:
                        logger.warning("提取抖音二维码异常 (用户可随时展开独立窗口): %s", qr_err)
                        self._qr_error = str(qr_err)
                    finally:
                        self._qr_ready_event.set()

                    found = False
                    for _ in range(120):
                        if self._active_context is None or self._logout_requested.is_set():
                            return  # 外部取消
                        try:
                            cookies = context.cookies()
                            has_login = any(
                                c.get("name") in {"sessionid", "sid_guard"} for c in cookies
                            )
                            if has_login:
                                found = True
                                break
                        except Exception:
                            break
                        time.sleep(1)

                    if not found:
                        self.status = "failed"
                        self.message = "登录超时（120秒）或浏览器已关闭，请重试"
                        return

                    if self._logout_requested.is_set():
                        return

                    # === 扫码成功瞬间立即持久化凭证！===
                    # 用户手机端确认扫码后 Cookie 已经注入 context，立即保存 storage_state 与 Cookie 文件，
                    # 避免后续抓取过程发生网络波动、或者用户提前关闭弹窗导致登录态丢失！
                    try:
                        self._save_storage_state(context)
                        logger.info("已在扫码成功瞬间安全持久化登录凭据")
                    except Exception as err:
                        logger.warning("即刻持久化登录凭据异常: %s", err)

                    self._profile = self._extract_profile(page)

                    # 2. 状态切换为 syncing
                    logger.info("扫码登录成功，立即开始抓取收藏夹...")
                    self.status = "syncing"
                    self.message = "扫码成功，正在同步收藏夹及视频列表..."

                    # 3. 立即在同一个 context 中抓数据
                    try:
                        snapshot = self._fetch_in_context(page)
                        if self._logout_requested.is_set():
                            return
                        self._snapshot = snapshot
                        logger.info("登录+抓取全部完成: %d 收藏夹, %d 视频", len(snapshot.collections), len(snapshot.videos))

                        # 4. 立即将快照持久化存入本地 SQLite 数据库！
                        try:
                            from app.db.session import session_factory
                            from app.services.favorites_service import favorites_service
                            with session_factory() as db:
                                favorites_service.save_snapshot_to_db(db, snapshot)
                            logger.info("快照已成功写入 SQLite 数据库")
                        except Exception as db_err:
                            logger.error("自动持久化到数据库失败: %s", db_err)

                        # 5. 保存登录态并导出 Cookie.  A logout can race the
                        # background browser thread; never recreate credentials
                        # after that request has been accepted.
                        if self._logout_requested.is_set():
                            return
                        try:
                            self._save_storage_state(context)
                        except Exception as err:
                            logger.warning("保存登录状态或导出 Cookie 异常: %s", err)

                        self.status = "logged_in"
                        self.message = f"登录成功，已同步 {len(snapshot.collections)} 个收藏夹，{len(snapshot.videos)} 个视频"
                    except Exception as exc:
                        logger.exception("抓取收藏夹失败")
                        if self._logout_requested.is_set():
                            return
                        try:
                            self._save_storage_state(context)
                        except Exception:
                            pass
                        self.status = "logged_in"
                        self.message = f"登录成功，但同步收藏夹部分失败: {str(exc)[:100]}"
                finally:
                    if context:
                        try:
                            context.close()
                        except Exception:
                            pass
                    self._active_context = None
                    self._active_playwright = None
                    self._active_page = None
                    self._cdp_session = None
                    self._window_id = None
                    self._qr_ready_event.set()
        except Exception as exc:
            # Logout is allowed to race browser startup/navigation.  In that
            # case the request already cleared credentials and selected idle;
            # do not let the retiring worker overwrite it with "failed".
            if self._logout_requested.is_set():
                logger.info("登录流程在退出请求后结束")
                return
            logger.exception("登录流程异常")
            self.status = "failed"
            self.message = str(exc)[:500]
        finally:
            self._lock.release()

    def _fetch_in_context(self, page) -> FavoriteScrapeSnapshot:
        """
        在已有的浏览器页面中通过 Webpack API 抓取收藏夹数据

        :param page: 已登录的 Playwright Page
        :return: 收藏夹快照
        """
        # 直接前往个人收藏夹页面，采用 commit 等待级别，坚决杜绝因主页视频流无限加载导致 120s 超时
        target_url = f"{settings.douyin_home_url.rstrip('/')}/user/self?showTab=favorite_collection"
        logger.info("直接导航至个人收藏夹页面: %s", target_url)
        try:
            page.goto(target_url, timeout=35_000, wait_until="commit")
        except Exception as nav_err:
            logger.warning("导航至收藏夹页面偶发超时: %s，尝试继续探测", nav_err)

        time.sleep(2.5)

        # 智能等待并定位 Webpack 收藏夹 API 模块
        logger.info("正在探测 Webpack 收藏夹 API 模块...")
        mid = None
        for attempt in range(25):
            if self._logout_requested.is_set():
                raise RuntimeError("抓取流程已取消")
            try:
                mid = page.evaluate("""
                    () => {
                        const chunks = window.webpackChunkdouyin_web;
                        if (!chunks || !Array.isArray(chunks)) return null;
                        const req = chunks.push([[Symbol("c")], {}, r => r]);
                        try { chunks.pop(); } catch(e) {}
                        if (!req || !req.m) return null;
                        for (const [id, mod] of Object.entries(req.m)) {
                            let src = "";
                            try { src = Function.prototype.toString.call(mod); } catch(e) { continue; }
                            if (src.includes("/aweme/v1/web/collects/list/") && src.includes("/aweme/v1/web/collects/video/list/")) {
                                return id;
                            }
                        }
                        return null;
                    }
                """)
                if mid:
                    logger.info("成功定位 Webpack 收藏夹模块 (mid=%s, attempt=%d)", mid, attempt)
                    break
            except Exception:
                pass

            # 若前 3 秒尚未找到，尝试模拟点击页面上的“收藏”Tab以触发模块动态加载
            if attempt == 3:
                try:
                    fav_tab = page.locator('span:text-is("收藏"), [data-e2e*="tab"]:has-text("收藏")').first
                    if fav_tab.is_visible(timeout=1000):
                        fav_tab.click(force=True)
                        logger.info("已尝试模拟点击收藏 Tab 触发模块加载")
                except Exception:
                    pass

            time.sleep(1.0)

        # 如果前 25 秒仍未就绪，尝试轻量 reload 一次并重试 15 秒
        if not mid:
            logger.warning("未立即定位到 collects 模块，尝试页面轻量重载...")
            try:
                page.reload(timeout=25_000, wait_until="commit")
                time.sleep(3.0)
                for _ in range(15):
                    if self._logout_requested.is_set():
                        raise RuntimeError("抓取流程已取消")
                    mid = page.evaluate("""
                        () => {
                            const chunks = window.webpackChunkdouyin_web;
                            if (!chunks || !Array.isArray(chunks)) return null;
                            const req = chunks.push([[Symbol("c")], {}, r => r]);
                            try { chunks.pop(); } catch(e) {}
                            if (!req || !req.m) return null;
                            for (const [id, mod] of Object.entries(req.m)) {
                                let src = "";
                                try { src = Function.prototype.toString.call(mod); } catch(e) { continue; }
                                if (src.includes("/aweme/v1/web/collects/list/") && src.includes("/aweme/v1/web/collects/video/list/")) {
                                    return id;
                                }
                            }
                            return null;
                        }
                    """)
                    if mid:
                        logger.info("刷新后成功定位 Webpack 收藏夹模块 (mid=%s)", mid)
                        break
                    time.sleep(1.0)
            except Exception as reload_err:
                logger.warning("刷新页面探测异常: %s", reload_err)

        if not mid:
            raise RuntimeError("抖音页面加载失败：收藏夹 API 模块未检测到，请确认网络正常后重试")

        # 执行 JS 调用 Webpack collects 模块
        result = page.evaluate("""
            async (targetMid) => {
                // 固定递增退避（不是可配置项，就是这三个字面量常量）：3 次
                // 尝试之间的 2 个间隔，仅在还有下一次尝试时才等待——最后一次
                // 失败后直接跳出循环报错，不再白等一次。抛异常和拿到非零
                // statusCode 走同一条退避判断，不再只有 catch 里才等待。
                const RETRY_DELAYS_MS = [500, 1000];
                const chunks = window.webpackChunkdouyin_web;
                if (!Array.isArray(chunks)) return {ok:false, error:"no_webpack"};
                const req = chunks.push([[Symbol("c")], {}, r => r]);
                try { chunks.pop(); } catch(e) {}
                if (!req || !req.m) return {ok:false, error:"no_require"};

                const api = req(Number(targetMid));
                if (!api) return {ok:false, error:"bad_module_id"};

                // 智能查找 API 函数
                let listFn = null, videoFn = null;
                for (const key of Object.keys(api)) {
                    const v = api[key];
                    if (typeof v !== 'function') continue;
                    try {
                        const src = Function.prototype.toString.call(v);
                        if (!listFn && src.includes('collects/list')) listFn = v;
                        if (!videoFn && src.includes('collects/video/list')) videoFn = v;
                    } catch(e) {}
                }
                if (!listFn) listFn = api.So;
                if (!videoFn) videoFn = api.d6;
                if (typeof listFn !== "function" || typeof videoFn !== "function")
                    return {ok:false, error:"bad_exports", keys:Object.keys(api||{}).slice(0,20)};

                // 拉收藏夹列表（支持弱网重试）
                const collections = [];
                let cursor = 0, guard = 0;
                while (guard < 30 && collections.length < 100) {
                    guard++;
                    let r = null;
                    for (let retry = 0; retry < 3; retry++) {
                        try {
                            r = await listFn({cursor, offset:30});
                            if (r && r.statusCode === 0) break;
                        } catch(e) {
                            r = null;
                        }
                        if (retry < 2) {
                            await new Promise(res => setTimeout(res, RETRY_DELAYS_MS[retry]));
                        }
                    }
                    if (!r || r.statusCode !== 0) {
                        if (collections.length > 0) break;
                        return {ok:false, error:"list_status", statusCode:r?.statusCode, msg:r?.statusMsg};
                    }
                    for (const c of (Array.isArray(r.data) ? r.data : []))
                        if (c && c.collectionFolderId) collections.push(c);
                    cursor = Number(r.cursor || 0);
                    if (!r.hasMore) break;
                }

                // 拉每个收藏夹的视频（支持弱网重试）
                const byCol = {};
                let totalInvalidCount = 0;
                for (const c of collections) {
                    const cid = String(c.collectionFolderId);
                    const rows = [];
                    const seen = new Set();
                    let cCur = 0, cG = 0;
                    while (cG < 120 && rows.length < 500) {
                        cG++;
                        let vr = null;
                        for (let retry = 0; retry < 3; retry++) {
                            try {
                                vr = await videoFn({collectsId:cid, cursor:cCur, offset:20});
                                if (vr && vr.statusCode === 0) break;
                            } catch(e) {
                                vr = null;
                            }
                            if (retry < 2) {
                                await new Promise(res => setTimeout(res, RETRY_DELAYS_MS[retry]));
                            }
                        }
                        if (!vr || vr.statusCode !== 0) break;
                        for (const v of (Array.isArray(vr.data) ? vr.data : [])) {
                            const vid = String(v?.awemeId || v?.groupId || "").trim();
                            if (!vid || seen.has(vid)) continue;
                            seen.add(vid);

                            // 严格过滤失效、私密、已删除、已下架或状态异常作品
                            const st = v?.status || {};
                            const isDel = st.is_delete === true || st.is_prohibited === true;
                            const isPriv = st.private_status === 1 || st.part_see === 1;
                            const tStr = String(v?.itemTitle || v?.desc || "");
                            const isBadTitle = tStr.includes("已失效") || tStr.includes("已下架") || tStr.includes("已被删除");
                            if (isDel || isPriv || isBadTitle) {
                                totalInvalidCount++;
                                continue;
                            }

                            rows.push({
                                awemeId:vid,
                                title:String(v?.itemTitle||v?.desc||"Untitled"),
                                author:String(v?.authorInfo?.nickname||""),
                                durationMs:Number(v?.video?.duration||0)
                            });
                        }
                        cCur = Number(vr.cursor || 0);
                        if (!vr.hasMore) break;
                    }
                    byCol[cid] = rows;
                }
                return {ok:true, collections, itemsByCollection:byCol, invalidCount: totalInvalidCount};
            }
        """, mid)

        if not isinstance(result, dict) or not result.get("ok"):
            error = result.get("error", "unknown") if isinstance(result, dict) else str(result)
            raise RuntimeError(f"Webpack 模块调用失败: {error}")

        # 解析结果
        collections: list[FavoriteScrapedCollection] = []
        videos_by_id: dict[str, FavoriteScrapedVideo] = {}

        for col in (result.get("collections") or []):
            cid = str(col.get("collectionFolderId") or "").strip()
            if not cid:
                continue
            collections.append(FavoriteScrapedCollection(
                platform_collection_id=cid,
                title=str(col.get("collectionFolderName") or "收藏夹")[:255],
                video_count=max(int(col.get("videoTotal") or 0), 0),
                cover_url=col.get("cover"),
            ))
            rows = (result.get("itemsByCollection") or {}).get(cid, [])
            for row in (rows or []):
                aid = str(row.get("awemeId") or "").strip()
                if not aid or not aid.isdigit():
                    continue
                if aid not in videos_by_id:
                    videos_by_id[aid] = FavoriteScrapedVideo(
                        platform_item_id=aid,
                        url=f"https://www.douyin.com/video/{aid}",
                        title=str(row.get("title") or "Untitled")[:500],
                        author=str(row.get("author") or "").strip()[:255],
                        duration=self._duration_to_seconds(row.get("durationMs")),
                    )
                    videos_by_id[aid].collection_ids.add(cid)

        reported_total = sum(c.video_count for c in collections)
        actual_total = len(videos_by_id)
        missing_diff = max(0, reported_total - actual_total)
        invalid_count = max(int(result.get("invalidCount") or 0), missing_diff)

        return FavoriteScrapeSnapshot(
            collections=collections,
            videos=list(videos_by_id.values()),
            invalid_count=invalid_count,
        )

    @staticmethod
    def _duration_to_seconds(raw) -> Optional[int]:
        if raw is None:
            return None
        try:
            d = int(raw)
        except (TypeError, ValueError):
            return None
        if d <= 0:
            return None
        return d // 1000 if d > 1000 else d

    # ------------------------------------------------------------------
    # 同步接口（返回缓存的快照）
    # ------------------------------------------------------------------

    def scrape_favorites_sync(self) -> FavoriteScrapeSnapshot:
        """
        使用本地已有的持久化 Cookie，直接在后台静默浏览器中抓取最新的收藏夹和视频数据并存入数据库

        无需用户重新扫码，完全后台静默执行 (headless=True)。
        """
        acquired = self._lock.acquire(blocking=True, timeout=60.0)
        if not acquired:
            raise RuntimeError("当前有采集或同步任务正在进行中，请稍候再试")
        try:
            with sync_playwright() as p:
                self._active_playwright = p
                # 始终以无头 (headless) 静默模式执行同步，避免弹出 Edge 窗口
                kwargs = self._browser_launch_kwargs(headless=True)
                logger.info("启动收藏夹实时同步静默抓取浏览器: %s", kwargs)
                context = None
                try:
                    context = p.chromium.launch_persistent_context(
                        user_data_dir=str(self.user_data_dir),
                        **kwargs,
                    )
                    self._active_context = context
                    page = context.pages[0] if context.pages else context.new_page()

                    snapshot = self._fetch_in_context(page)
                    self._snapshot = snapshot
                    logger.info("实时抓取完成: %d 收藏夹, %d 视频", len(snapshot.collections), len(snapshot.videos))

                    # 自动持久化写入数据库
                    try:
                        from app.db.session import session_factory
                        from app.services.favorites_service import favorites_service
                        with session_factory() as db:
                            favorites_service.save_snapshot_to_db(db, snapshot)
                        logger.info("实时抓取的收藏夹快照已成功持久化到数据库")
                    except Exception as db_err:
                        logger.error("自动持久化到数据库失败: %s", db_err)

                    # 刷新 state.json 与 Cookie 文件
                    try:
                        self._save_storage_state(context)
                    except Exception:
                        pass

                    self.status = "logged_in"
                    self.message = f"已就绪，已同步 {len(snapshot.collections)} 个收藏夹，{len(snapshot.videos)} 个视频"
                    return snapshot
                finally:
                    if context:
                        try:
                            context.close()
                        except Exception:
                            pass
                    self._active_context = None
                    self._active_playwright = None
        except Exception as exc:
            logger.exception("实时同步收藏夹异常")
            raise RuntimeError(f"同步收藏夹失败: {exc}")
        finally:
            self._lock.release()

    async def fetch_snapshot(
        self,
        max_collections: int = 100,
        max_videos_per_collection: int = 500,
        force: bool = False,
    ) -> FavoriteScrapeSnapshot:
        """
        获取抖音收藏夹快照：
        1. `force=False` 且内存快照存在且包含有效视频 → 直接复用（登录流程等被动调用用）；
        2. `force=True`（用户主动点「同步」）→ 跳过缓存，用已保存的 Cookie 实时重抓；
        3. 若实时抓取异常，回退尝试从已有数据库记录恢复兜底。
        """
        if not force and self._snapshot is not None and len(self._snapshot.videos) > 0:
            return self._snapshot

        if not self._check_saved_login():
            raise RuntimeError("尚未登录或登录已失效，请先扫码登录抖音")

        # 尝试使用已保存的 Cookie 实时抓取同步
        try:
            return await asyncio.to_thread(self.scrape_favorites_sync)
        except Exception as sync_err:
            logger.warning("实时同步抓取异常，尝试从数据库重构已有记录兜底: %s", sync_err)

        # 兜底：尝试从本地数据库中恢复已有快照
        try:
            from app.db.session import session_factory
            from app.models.entities import FavoriteCollection, FavoriteVideo
            from sqlalchemy import select
            with session_factory() as db:
                db_cols = db.execute(select(FavoriteCollection).where(FavoriteCollection.is_active.is_(True))).scalars().all()
                if db_cols:
                    collections = [
                        FavoriteScrapedCollection(
                            platform_collection_id=c.platform_collection_id,
                            title=c.title,
                            video_count=c.video_count,
                            cover_url=getattr(c, "cover_url", None),
                        )
                        for c in db_cols
                    ]
                    col_id_map = {c.id: c.platform_collection_id for c in db_cols}
                    db_vids = db.execute(select(FavoriteVideo).where(FavoriteVideo.is_active.is_(True))).scalars().all()
                    v_dict: dict[str, FavoriteScrapedVideo] = {}
                    for v in db_vids:
                        if v.platform_item_id not in v_dict:
                            v_dict[v.platform_item_id] = FavoriteScrapedVideo(
                                platform_item_id=v.platform_item_id,
                                url=v.video_url,
                                title=v.title,
                                author=v.author or "",
                                duration=v.duration,
                            )
                        plat_cid = col_id_map.get(v.collection_id)
                        if plat_cid:
                            v_dict[v.platform_item_id].collection_ids.add(plat_cid)
                    self._snapshot = FavoriteScrapeSnapshot(collections=collections, videos=list(v_dict.values()))
                    return self._snapshot
        except Exception as err:
            logger.warning("从数据库重构快照失败: %s", err)

        raise RuntimeError("未检测到已同步的收藏夹数据，请在打开的窗口中完成扫码同步")

    # ------------------------------------------------------------------
    # 登出
    # ------------------------------------------------------------------

    def logout(self) -> tuple[bool, str]:
        """Clear local credentials without synchronously deleting locked Chromium files.

        On Windows a Playwright child can retain a profile handle briefly after
        context.close().  Move the profile out of the active path first and
        clean it in a bounded background retry loop instead of failing logout.
        """
        self._logout_requested.set()
        if self._login_task and not self._login_task.done():
            self._login_task.cancel()
            self._login_task = None

        # The sync Playwright context belongs to the login worker thread.
        # Calling close() from this request thread can deadlock on Windows.
        # Clearing this signal makes that worker return and close it in its own
        # finally block.
        self._active_context = None

        errors: list[str] = []
        try:
            delete_json(self.storage_state_path)
        except Exception as exc:
            errors.append(f"删除登录态失败: {exc}")

        # 清理导出的 douyin_cookies.txt
        try:
            cookie_path = Path(settings.audio_cache_dir) / "douyin_cookies.txt"
            if cookie_path.exists():
                cookie_path.unlink(missing_ok=True)
        except Exception:
            pass

        user_data_dir = self.user_data_dir
        trash_dir: Path | None = None
        locked_profile_dir: Path | None = None
        try:
            if user_data_dir.exists():
                trash_dir = user_data_dir.with_name(
                    f"{user_data_dir.name}.trash_{time.time_ns()}"
                )
                try:
                    user_data_dir.replace(trash_dir)
                except OSError as exc:
                    # Do not let a subsequent login reuse this still-live
                    # Chromium profile.  The owning worker will close its
                    # context shortly; a bounded retry loop removes it then.
                    logger.warning("浏览器档案暂时被锁定，等待后台回收: %s", exc)
                    trash_dir = None
                    locked_profile_dir = user_data_dir
            user_data_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            errors.append(f"初始化新的浏览器档案失败: {exc}")

        if trash_dir is not None or locked_profile_dir is not None:
            cleanup_target = trash_dir or locked_profile_dir
            if locked_profile_dir is not None:
                self._profile_cleanup_pending.set()

            def _cleanup_profile(path: Path, recreate: bool) -> bool:
                for attempt in range(10):
                    try:
                        shutil.rmtree(path)
                        if recreate:
                            path.mkdir(parents=True, exist_ok=True)
                        return True
                    except OSError as exc:
                        logger.info("延后回收浏览器档案失败（第 %d 次）: %s", attempt + 1, exc)
                        time.sleep(min(0.5 * (attempt + 1), 3.0))
                logger.error("浏览器档案仍被占用，已阻止重新登录以避免旧会话复活: %s", path)
                return False

            def _cleanup_and_release(path: Path, recreate: bool) -> None:
                completed = _cleanup_profile(path, recreate)
                if recreate and completed:
                    self._profile_cleanup_pending.clear()

            threading.Thread(
                target=_cleanup_and_release,
                args=(cleanup_target, locked_profile_dir is not None),
                daemon=True,
                name="douyin-profile-cleanup",
            ).start()

        self._snapshot = None
        self._profile = {}
        self._qr_image_base64 = None
        self._qr_ready_event.clear()
        self._active_page = None
        self._cdp_session = None
        self._window_id = None
        if errors:
            self.status = "failed"
            self.message = "; ".join(errors)[:1000]
            return False, self.message

        self.status = "idle"
        self.message = "已退出登录"
        return True, self.message


# 全局单例
collector = DouyinCollector()
