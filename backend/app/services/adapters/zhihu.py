"""知乎适配器兼容层。

知乎的实际生产入口是 ``ZhihuCollector`` + ``FavoritesService``；这个适配器只
实现既有抽象接口，避免注册表与遗留适配器工厂对已接入平台产生漂移。
"""
from __future__ import annotations

import asyncio
from typing import Optional

from app.services.adapters.base import (
    AuthStatus,
    BasePlatformAdapter,
    PlatformCollection,
    PlatformItem,
    QRCodeInfo,
    QRCheckResult,
)
from app.services.platform_registry import UnsupportedPlatformError, build_canonical_url
from app.services.zhihu_collector import FavoriteScrapeSnapshot, zhihu_collector


class ZhihuAdapter(BasePlatformAdapter):
    """将知乎浏览器会话映射到旧适配器协议。"""

    def __init__(self) -> None:
        self._snapshot: FavoriteScrapeSnapshot | None = None

    @property
    def platform_name(self) -> str:
        return "zhihu"

    async def check_auth(self) -> AuthStatus:
        status = zhihu_collector.get_status()
        return AuthStatus(
            platform="zhihu",
            is_logged_in=status["status"] in ("logged_in", "syncing"),
            nickname=status.get("nickname", ""),
            avatar_url=status.get("avatar_url", ""),
            error_message=status.get("message", ""),
        )

    async def get_qr_code(self) -> QRCodeInfo:
        raise UnsupportedPlatformError("知乎使用可见浏览器窗口登录，不提供二维码接口")

    async def check_qr_status(self, qrcode_key: str) -> QRCheckResult:
        raise UnsupportedPlatformError("知乎使用浏览器登录，不支持二维码状态轮询")

    async def _snapshot_or_fetch(self) -> FavoriteScrapeSnapshot:
        self._snapshot = await asyncio.to_thread(zhihu_collector.sync_favorites_sync)
        return self._snapshot

    async def fetch_collections(self) -> list[PlatformCollection]:
        snapshot = await self._snapshot_or_fetch()
        return [
            PlatformCollection(
                platform="zhihu",
                remote_collection_id=item.platform_collection_id,
                title=item.title,
                item_count=item.video_count,
                cover_url=item.cover_url or "",
            )
            for item in snapshot.collections
        ]

    async def fetch_collection_items(
        self,
        remote_collection_id: str,
        cursor: int = 0,
        page_size: int = 20,
    ) -> tuple[list[PlatformItem], bool, int]:
        snapshot = await self._snapshot_or_fetch()
        items = [item for item in snapshot.videos if remote_collection_id in item.collection_ids]
        start = max(cursor, 0)
        page = items[start : start + page_size]
        next_cursor = start + len(page)
        return [
            PlatformItem(
                platform="zhihu",
                remote_item_id=item.platform_item_id,
                title=item.title,
                author=item.author,
                duration=0,
                canonical_url=build_canonical_url("zhihu", item.platform_item_id, item.url),
                item_type="note",
            )
            for item in page
        ], next_cursor < len(items), next_cursor

    async def fetch_item_content(
        self,
        remote_item_id: str,
        part_id: int = 0,
        item_meta: Optional[PlatformItem] = None,
    ) -> str:
        snapshot = self._snapshot or await self._snapshot_or_fetch()
        item = next((value for value in snapshot.videos if value.platform_item_id == remote_item_id), None)
        if item is None or not item.text_content.strip():
            raise RuntimeError(f"知乎内容正文不可用: {remote_item_id}")
        return item.text_content
