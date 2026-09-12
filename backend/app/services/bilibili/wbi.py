"""
Bilibili WBI 签名服务模块

防刷新风暴与并发竞争设计：
1. 单飞互斥锁 (asyncio.Lock)：防止多并发请求同时穿透拉取 nav 接口
2. 长效缓存：默认缓存 12 小时 (wbi_cache_ttl_hours)，绝不因请求携带 cookies 而无条件刷新
3. 容灾兜底：网络异常时复用上次有效 key
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from functools import reduce
from typing import Optional
from urllib.parse import urlencode

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# WBI 签名混淆重排索引表
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]


class WbiSigner:
    """B 站 WBI 签名器 (并发安全、低开销)"""

    def __init__(self, ttl_hours: float | None = None) -> None:
        self._locks: dict[int, asyncio.Lock] = {}
        self._mixin_key: Optional[str] = None
        self._last_update: float = 0.0
        self._ttl_seconds: float = (ttl_hours or settings.wbi_cache_ttl_hours) * 3600.0

    def _lock_for_current_loop(self) -> asyncio.Lock:
        key = id(asyncio.get_running_loop())
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    @staticmethod
    def _get_mixin_key(orig: str) -> str:
        """根据官方混淆表生成 mixin_key"""
        return reduce(lambda s, i: s + orig[i], MIXIN_KEY_ENC_TAB, "")[:32]

    @staticmethod
    def _filter_params(params: dict) -> dict:
        """过滤官方规约的非法字符: ! ' ( ) *"""
        return {
            k: "".join(c for c in str(v) if c not in "!'()*")
            for k, v in params.items()
        }

    async def ensure_keys(
        self,
        client: Optional[httpx.AsyncClient] = None,
        force_refresh: bool = False,
    ) -> str:
        """
        获取或刷新 mixin_key (并发安全)

        :param client: 可选复用的 httpx.AsyncClient
        :param force_refresh: 是否强制刷新 (仅在遇到签名过期明确报错时使用)
        :return: 32位 mixin_key
        """
        now = time.time()
        if (
            not force_refresh
            and self._mixin_key is not None
            and (now - self._last_update) < self._ttl_seconds
        ):
            return self._mixin_key

        async with self._lock_for_current_loop():
            # 双重检查
            now = time.time()
            if (
                not force_refresh
                and self._mixin_key is not None
                and (now - self._last_update) < self._ttl_seconds
            ):
                return self._mixin_key

            logger.info("正在更新 Bilibili WBI 签名秘钥 (单飞模式)...")
            should_close = False
            http = client
            if http is None:
                http = httpx.AsyncClient(timeout=10.0)
                should_close = True

            try:
                resp = await http.get(
                    "https://api.bilibili.com/x/web-interface/nav",
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Safari/537.36"
                        ),
                        "Referer": "https://www.bilibili.com/",
                    },
                )
                data = resp.json()
                if data.get("code") != 0:
                    raise RuntimeError(f"B站 nav 接口返回异常: {data}")

                wbi_img = data["data"]["wbi_img"]
                img_key = wbi_img["img_url"].rsplit("/", 1)[1].split(".")[0]
                sub_key = wbi_img["sub_url"].rsplit("/", 1)[1].split(".")[0]
                new_key = self._get_mixin_key(img_key + sub_key)
                self._mixin_key = new_key
                self._last_update = time.time()
                logger.info("Bilibili WBI 秘钥更新成功 (有效期 %d 小时)", int(self._ttl_seconds / 3600))
                return new_key
            except Exception as exc:
                if self._mixin_key is not None:
                    logger.warning("刷新 WBI 秘钥失败，继续沿用上一次有效秘钥: %s", exc)
                    return self._mixin_key
                raise RuntimeError(f"初始化 Bilibili WBI 签名秘钥失败: {exc}") from exc
            finally:
                if should_close:
                    await http.aclose()

    async def sign(
        self,
        params: dict,
        client: Optional[httpx.AsyncClient] = None,
        force_refresh: bool = False,
    ) -> dict:
        """
        对请求参数附加 WBI 签名与时间戳

        :param params: 待签名原始参数字典
        :param client: 可选复用的 httpx 客户端
        :param force_refresh: 是否强制刷新密钥
        :return: 包含 w_rid 与 wts 的签名参数字典
        """
        mixin_key = await self.ensure_keys(client=client, force_refresh=force_refresh)
        clean_params = self._filter_params(params)
        clean_params["wts"] = int(time.time())
        sorted_params = dict(sorted(clean_params.items()))
        query_str = urlencode(sorted_params)
        w_rid = hashlib.md5((query_str + mixin_key).encode("utf-8")).hexdigest()
        sorted_params["w_rid"] = w_rid
        return sorted_params


# 全局单例
wbi_signer = WbiSigner()
