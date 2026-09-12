"""
Bilibili API 客户端封装

设计要点：
1. 信号量并发控制：限制针对 B 站的上行并发数 (bilibili_max_concurrency)
2. 连接池与会话复用：复用单个 AsyncClient 降低握手开销
3. 状态原子持久化：保存/加载 SESSDATA、bili_jct、DedeUserID 等到 bilibili_state.json
4. 支持二维码登录、收藏夹拉取、分P解析与音视频提取
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import tempfile
import time
import urllib.parse
import weakref
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import qrcode

from app.core.config import settings
from app.services.adapters.base import AuthStatus, QRCodeInfo, QRCheckResult
from app.services.bilibili.wbi import wbi_signer

logger = logging.getLogger(__name__)


def _is_trusted_bilibili_url(value: str, *, subtitle: bool = False) -> bool:
    """Reject local/foreign URLs before downloading provider-controlled media."""
    parsed = urllib.parse.urlsplit(value if not value.startswith("//") else f"https:{value}")
    host = (parsed.hostname or "").lower()
    allowed = ("bilibili.com", "bilivideo.com", "bilivideo.cn")
    return parsed.scheme == "https" and any(host == suffix or host.endswith(f".{suffix}") for suffix in allowed)


class BilibiliClient:
    """B 站 Web API 客户端 (并发受控与持久化管理)"""

    BASE_URL = "https://api.bilibili.com"
    PASSPORT_URL = "https://passport.bilibili.com"
    DEFAULT_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.bilibili.com/",
        "Origin": "https://www.bilibili.com",
    }

    def __init__(self) -> None:
        p = Path(settings.bilibili_state_path)
        if not p.is_absolute():
            candidate = Path(__file__).resolve().parent.parent.parent / p
            if candidate.exists() or not p.exists():
                p = candidate
        self._state_path = p
        self._cookies: dict[str, str] = {}
        self._user_info: dict[str, Any] = {}
        # WeakKeyDictionary 而不是 id(loop) -> value：CPython 的 id() 是内存
        # 地址，事件循环对象被垃圾回收后地址可能被新循环复用，用 int 键的
        # 普通 dict 会让新循环命中一个绑定在已关闭循环上的 client/信号量。
        # WeakKeyDictionary 直接以循环对象本身为键，随对象被 GC 自动清除
        # 条目，从根上不存在这类复用问题。
        self._clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, httpx.AsyncClient]" = weakref.WeakKeyDictionary()
        self._semaphores: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = weakref.WeakKeyDictionary()
        self._load_state()

    def _get_client(self) -> httpx.AsyncClient:
        """Get a client that belongs to the current event loop."""
        loop = asyncio.get_running_loop()
        client = self._clients.get(loop)
        if client is None or client.is_closed:
            client = httpx.AsyncClient(
                timeout=httpx.Timeout(25.0, connect=10.0, read=25.0, write=15.0),
                headers=self.DEFAULT_HEADERS,
                follow_redirects=True,
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=15, keepalive_expiry=15.0),
            )
            self._clients[loop] = client
        return client

    def _get_semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        semaphore = self._semaphores.get(loop)
        if semaphore is None:
            semaphore = asyncio.Semaphore(settings.bilibili_max_concurrency)
            self._semaphores[loop] = semaphore
        return semaphore

    async def _request(
        self,
        method: str,
        url: str,
        *,
        max_retries: int = 3,
        **kwargs,
    ) -> httpx.Response:
        """统一请求执行器：提供并发信号量管控、重试机制及连接池自动回收。"""
        loop = asyncio.get_running_loop()
        last_exc: Optional[Exception] = None

        for attempt in range(1, max_retries + 1):
            client = self._get_client()
            try:
                async with self._get_semaphore():
                    handler = getattr(client, method.lower(), None)
                    if callable(handler):
                        resp = await handler(url, **kwargs)
                    else:
                        resp = await client.request(method, url, **kwargs)
                    return resp
            except (
                httpx.ConnectTimeout,
                httpx.ReadTimeout,
                httpx.WriteTimeout,
                httpx.PoolTimeout,
                httpx.ConnectError,
                httpx.RemoteProtocolError,
            ) as exc:
                last_exc = exc
                logger.warning(
                    "B站网络请求异常 (%d/%d) [%s]: %s",
                    attempt,
                    max_retries,
                    url,
                    type(exc).__name__,
                )
                if loop in self._clients:
                    old_client = self._clients.pop(loop)
                    if not old_client.is_closed:
                        try:
                            await old_client.aclose()
                        except Exception:
                            pass
                if attempt < max_retries:
                    await asyncio.sleep(0.5 * attempt)
            except Exception:
                raise

        if last_exc:
            raise last_exc
        raise RuntimeError(f"请求失败: {url}")

    async def aclose(self) -> None:
        """关闭所有事件循环所属的连接池。"""
        clients = list(self._clients.values())
        self._clients.clear()
        self._semaphores.clear()
        for client in clients:
            if not client.is_closed:
                await client.aclose()

    # ==================================================================
    # 状态持久化与 Cookie 管理
    # ==================================================================

    def _load_state(self) -> None:
        """从状态文件中读取保存的凭据与用户信息"""
        try:
            if self._state_path.exists():
                data = json.loads(self._state_path.read_text(encoding="utf-8"))
                self._cookies = data.get("cookies", {})
                self._user_info = data.get("user_info", {})
                logger.debug("已载入 Bilibili 登录态: mid=%s", self._user_info.get("mid"))
        except Exception as exc:
            logger.warning("载入 Bilibili 状态文件失败: %s", exc)
            self._cookies = {}
            self._user_info = {}

    def _save_state(self) -> None:
        """原子写入状态文件"""
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "cookies": self._cookies,
                "user_info": self._user_info,
                "updated_at": int(time.time()),
            }
            # 原子写: 先写临时文件，再原子重命名
            tmp_fd, tmp_path = tempfile.mkstemp(
                dir=str(self._state_path.parent), prefix="bili_state_", suffix=".tmp"
            )
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, self._state_path)
            except Exception:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise
        except Exception as exc:
            logger.error("保存 Bilibili 状态文件失败: %s", exc)

    def clear_state(self) -> None:
        """清除本地登录凭据"""
        self._cookies = {}
        self._user_info = {}
        try:
            if self._state_path.exists():
                self._state_path.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("删除 Bilibili 状态文件失败: %s", exc)

    @property
    def is_logged_in(self) -> bool:
        """是否已配置有效 Session Cookie"""
        return bool(self._cookies.get("SESSDATA"))

    @property
    def dedeuserid(self) -> str:
        return self._cookies.get("DedeUserID", "")

    def _invalidate_if_unauthorized(self, data: dict[str, Any]) -> None:
        """Make a real provider -101 response immediately visible to the UI."""
        if data.get("code") == -101:
            logger.info("B站凭证已失效，清除本地会话")
            self.clear_state()

    # ==================================================================
    # 鉴权与二维码登录
    # ==================================================================

    async def get_auth_status(self, *, verify_remote: bool = False) -> AuthStatus:
        """Return cached auth state unless an explicit remote verification is requested.

        The UI polls this endpoint frequently.  Performing a 30-second remote
        ``nav`` request for every paint made local logout appear to hang.
        Provider operations still verify their own authorization and clear an
        expired session when Bilibili rejects it.
        """
        if not self.is_logged_in:
            return AuthStatus(
                platform="bilibili",
                is_logged_in=False,
                error_message="未登录",
            )
        if not verify_remote:
            return AuthStatus(
                platform="bilibili",
                is_logged_in=True,
                account_id=str(self._user_info.get("mid", "") or self.dedeuserid),
                nickname=self._user_info.get("uname", ""),
                avatar_url=self._user_info.get("face", ""),
            )
        try:
            info = await self.get_user_info()
            self._user_info = info
            self._save_state()
            return AuthStatus(
                platform="bilibili",
                is_logged_in=True,
                account_id=str(info.get("mid", "")),
                nickname=info.get("uname", ""),
                avatar_url=info.get("face", ""),
            )
        except Exception as exc:
            logger.warning("B站登录态校验失败: %s", exc)
            return AuthStatus(
                platform="bilibili",
                is_logged_in=False,
                error_message=str(exc),
            )

    async def generate_qrcode(self) -> QRCodeInfo:
        """申请登录二维码并在内存中生成 Base64 PNG 图片"""
        url = f"{self.PASSPORT_URL}/x/passport-login/web/qrcode/generate"
        resp = await self._request("GET", url)
        data = resp.json()

        if data.get("code") != 0:
            raise RuntimeError(f"生成 B站 二维码失败: {data.get('message')}")

        qr_data = data["data"]
        qrcode_key = qr_data["qrcode_key"]
        qrcode_url = qr_data["url"]

        # 生成二维码图片
        qr = qrcode.QRCode(version=1, box_size=8, border=2)
        qr.add_data(qrcode_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64_str = base64.b64encode(buf.getvalue()).decode("utf-8")

        return QRCodeInfo(
            platform="bilibili",
            qrcode_key=qrcode_key,
            qrcode_url=qrcode_url,
            qrcode_image_base64=b64_str,
            expires_in=180,
        )

    async def poll_qrcode_status(self, qrcode_key: str) -> QRCheckResult:
        """轮询二维码扫码确认状态"""
        url = f"{self.PASSPORT_URL}/x/passport-login/web/qrcode/poll"
        resp = await self._request("GET", url, params={"qrcode_key": qrcode_key})
        data = resp.json()

        if data.get("code") != 0:
            return QRCheckResult(
                platform="bilibili",
                status="error",
                message=data.get("message", "轮询失败"),
            )

        inner = data.get("data") or {}
        code = inner.get("code")
        raw_msg = inner.get("message", "")

        status_map = {
            86101: ("waiting", "等待扫码"),
            86090: ("scanned", "已扫码，等待在手机端确认"),
            86038: ("expired", "二维码已过期，请刷新"),
            0: ("confirmed", "登录成功"),
        }
        status, msg = status_map.get(code, ("unknown", raw_msg))

        if status == "confirmed":
            cookies: dict[str, str] = {}
            for cookie in resp.cookies.jar:
                cookies[cookie.name] = cookie.value

            url_str = inner.get("url", "")
            if "SESSDATA=" in url_str:
                parsed = urllib.parse.parse_qs(urllib.parse.urlparse(url_str).query)
                for k in ("SESSDATA", "bili_jct", "DedeUserID"):
                    if k in parsed and parsed[k]:
                        cookies[k] = parsed[k][0]

            self._cookies = cookies
            # 立即拉取并保存用户信息
            try:
                user_info = await self.get_user_info()
                self._user_info = user_info
                self._save_state()
                return QRCheckResult(
                    platform="bilibili",
                    status="confirmed",
                    message="登录成功",
                    account_id=str(user_info.get("mid", "")),
                    nickname=user_info.get("uname", ""),
                    avatar_url=user_info.get("face", ""),
                )
            except Exception as exc:
                logger.warning("获取登录后用户信息失败: %s", exc)
                self._save_state()
                return QRCheckResult(
                    platform="bilibili",
                    status="confirmed",
                    message="登录成功",
                    account_id=self.dedeuserid,
                )

        return QRCheckResult(platform="bilibili", status=status, message=msg)

    async def get_user_info(self) -> dict[str, Any]:
        """获取当前登录用户信息 (https://api.bilibili.com/x/web-interface/nav)"""
        url = f"{self.BASE_URL}/x/web-interface/nav"
        resp = await self._request("GET", url, cookies=self._cookies)
        data = resp.json()

        if data.get("code") != 0:
            self._invalidate_if_unauthorized(data)
            raise RuntimeError(f"获取用户信息失败: {data.get('message')}")
        return data.get("data") or {}

    # ==================================================================
    # 收藏夹与视频列表
    # ==================================================================

    async def get_user_favorites(self, mid: int | str | None = None) -> list[dict[str, Any]]:
        """获取用户创建的所有公开与私有收藏夹"""
        target_mid = mid or self.dedeuserid or self._user_info.get("mid")
        if not target_mid:
            # 尝试刷新用户信息
            info = await self.get_user_info()
            target_mid = info.get("mid")

        url = f"{self.BASE_URL}/x/v3/fav/folder/created/list-all"
        resp = await self._request("GET", url, params={"up_mid": target_mid}, cookies=self._cookies)
        data = resp.json()

        if data.get("code") != 0:
            self._invalidate_if_unauthorized(data)
            raise RuntimeError(f"获取收藏夹列表失败: {data.get('message')}")

        folder_list = (data.get("data") or {}).get("list") or []
        return folder_list

    async def get_favorite_content(
        self,
        media_id: int | str,
        pn: int = 1,
        ps: int = 20,
    ) -> dict[str, Any]:
        """
        分页拉取收藏夹下的视频资源

        :param media_id: 收藏夹 ID
        :param pn: 页码 (从 1 开始)
        :param ps: 每页数量 (B 站上限 20)
        :return: {"info": ..., "medias": [...], "has_more": bool}
        """
        url = f"{self.BASE_URL}/x/v3/fav/resource/list"
        params = {
            "media_id": media_id,
            "pn": pn,
            "ps": min(ps, 20),
            "platform": "web",
        }
        resp = await self._request("GET", url, params=params, cookies=self._cookies)
        data = resp.json()

        if data.get("code") != 0:
            self._invalidate_if_unauthorized(data)
            raise RuntimeError(f"获取收藏夹内容失败: {data.get('message')}")

        payload = data.get("data") or {}
        return {
            "info": payload.get("info") or {},
            "medias": payload.get("medias") or [],
            "has_more": bool(payload.get("has_more")),
        }

    async def get_video_info(self, bvid: str) -> dict[str, Any]:
        """获取视频基本元数据及分P列表 (pages)"""
        url = f"{self.BASE_URL}/x/web-interface/view"
        resp = await self._request("GET", url, params={"bvid": bvid}, cookies=self._cookies)
        data = resp.json()

        if data.get("code") != 0:
            self._invalidate_if_unauthorized(data)
            raise RuntimeError(f"获取视频信息失败 [{bvid}]: {data.get('message')}")
        return data.get("data") or {}

    async def get_player_info(self, bvid: str, cid: int) -> dict[str, Any]:
        """获取播放器元信息 (包括官方字幕与 AI 字幕)"""
        params = {"bvid": bvid, "cid": cid}

        # 优先使用 WBI 签名版，字幕命中率更高
        try:
            signed_params = await wbi_signer.sign(params, client=self._get_client())
            url = f"{self.BASE_URL}/x/player/wbi/v2"
            resp = await self._request("GET", url, params=signed_params, cookies=self._cookies)
            data = resp.json()
            if data.get("code") == 0 and data.get("data"):
                return data["data"]
        except Exception as exc:
            logger.debug("WBI 播放器信息获取失败，尝试常规接口: %s", exc)

        # 降级回退到普通 player/v2
        url = f"{self.BASE_URL}/x/player/v2"
        resp = await self._request("GET", url, params=params, cookies=self._cookies)
        data = resp.json()

        if data.get("code") != 0:
            self._invalidate_if_unauthorized(data)
            logger.warning("获取播放器字幕信息失败 [%s]: %s", bvid, data.get("message"))
            return {}
        return data.get("data") or {}

    async def get_audio_url(self, bvid: str, cid: int) -> Optional[str]:
        """
        获取音频流的 CDN 直链 (用于 ASR 音频转写)
        优先选择 <= 64kbps 的低带宽音频流以降低下载开销。
        """
        params = {
            "bvid": bvid,
            "cid": cid,
            "fnval": 16,  # 启用 DASH 格式
            "fnver": 0,
            "fourk": 1,
        }

        # 优先 WBI 签名接口
        data = None
        try:
            signed_params = await wbi_signer.sign(params, client=self._get_client())
            url = f"{self.BASE_URL}/x/player/wbi/playurl"
            resp = await self._request("GET", url, params=signed_params, cookies=self._cookies)
            data = resp.json()
        except Exception as exc:
            logger.debug("WBI playurl 获取异常: %s", exc)

        # 回退普通接口
        if not data or data.get("code") != 0:
            try:
                url = f"{self.BASE_URL}/x/player/playurl"
                resp = await self._request("GET", url, params=params, cookies=self._cookies)
                data = resp.json()
            except Exception as exc:
                logger.warning("playurl 获取失败 [%s]: %s", bvid, exc)
                return None

        if not data or data.get("code") != 0:
            if data:
                self._invalidate_if_unauthorized(data)
            logger.warning("获取视频播放流失败 [%s]: %s", bvid, (data or {}).get("message"))
            return None

        payload = data.get("data") or {}
        dash = payload.get("dash") or {}
        audio_list = dash.get("audio") or []

        if audio_list:
            def _bw(item: dict) -> int:
                try:
                    return int(item.get("bandwidth") or item.get("bandWidth") or 0)
                except Exception:
                    return 0

            # 优先选择 <= 64kbps 的音频流，既轻量又能被 ASR 高质量识别
            max_bw = 64_000
            candidates = [a for a in audio_list if _bw(a) > 0]
            if candidates:
                preferred = [a for a in candidates if _bw(a) <= max_bw]
                best = max(preferred, key=_bw) if preferred else min(candidates, key=_bw)
            else:
                best = audio_list[0]
            return best.get("baseUrl") or best.get("base_url") or best.get("url")

        durl = payload.get("durl") or []
        if durl:
            return durl[0].get("url")

        return None

    async def download_subtitle(self, subtitle_url: str) -> str:
        """下载并合并 B 站 JSON 格式的字幕为连贯文本"""
        if subtitle_url.startswith("//"):
            subtitle_url = f"https:{subtitle_url}"
        if not _is_trusted_bilibili_url(subtitle_url, subtitle=True):
            raise ValueError("B站字幕地址不受信任")

        resp = await self._request("GET", subtitle_url, follow_redirects=False)
        resp.raise_for_status()
        if "json" not in resp.headers.get("content-type", "").lower():
            raise ValueError("B站字幕响应不是 JSON")
        data = resp.json()

        body = data.get("body") or []
        texts: list[str] = []
        for item in body:
            content = (item.get("content") or "").strip()
            if content:
                texts.append(content)
        return "\n".join(texts)

    async def download_audio_to_file(self, audio_url: str, file_path: Path) -> bool:
        """流式分块下载音频直链到指定文件路径"""
        if not audio_url or not _is_trusted_bilibili_url(audio_url):
            return False

        client = self._get_client()
        headers = dict(self.DEFAULT_HEADERS)
        headers["Referer"] = "https://www.bilibili.com/"

        temporary = file_path.with_name(f"{file_path.name}.{os.getpid()}.download")
        max_bytes = int(settings.asr_max_audio_size_mb * 1024 * 1024)
        try:
            async with self._get_semaphore():
                async with client.stream(
                    "GET", audio_url, headers=headers, follow_redirects=False
                ) as resp:
                    if resp.status_code not in (200, 206):
                        logger.warning("下载 B 站音频失败: status=%s", resp.status_code)
                        return False
                    length = int(resp.headers.get("content-length") or 0)
                    if length <= 0 or length > max_bytes:
                        logger.warning("B站音频体积不合法: %s", length)
                        return False
                    content_type = resp.headers.get("content-type", "").lower()
                    if not content_type.startswith("audio/") and "octet-stream" not in content_type:
                        logger.warning("B站音频类型不合法: %s", content_type)
                        return False
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    written = 0
                    with open(temporary, "wb") as f:
                        async for chunk in resp.aiter_bytes():
                            if chunk:
                                written += len(chunk)
                                if written > max_bytes:
                                    raise ValueError("B站音频超过 ASR 大小上限")
                                f.write(chunk)
            if temporary.exists() and temporary.stat().st_size > 1024:
                temporary.replace(file_path)
                return True
            return False
        except Exception as exc:
            logger.warning("下载 B 站音频流异常: %s", exc)
            return False
        finally:
            temporary.unlink(missing_ok=True)


# 全局客户端实例
bilibili_client = BilibiliClient()

