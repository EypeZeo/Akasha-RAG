"""
Bilibili 平台适配器实现
"""
from __future__ import annotations

import logging
from typing import Optional

from app.services.adapters.base import (
    AuthStatus,
    BasePlatformAdapter,
    PlatformCollection,
    PlatformItem,
    PlatformItemPart,
    QRCodeInfo,
    QRCheckResult,
)
from app.services.bilibili.client import bilibili_client
from app.services.bilibili.content_fetcher import bilibili_content_fetcher

logger = logging.getLogger(__name__)


class BilibiliAdapter(BasePlatformAdapter):
    """Bilibili 跨平台适配器"""

    @property
    def platform_name(self) -> str:
        return "bilibili"

    async def check_auth(self) -> AuthStatus:
        return await bilibili_client.get_auth_status()

    async def get_qr_code(self) -> QRCodeInfo:
        return await bilibili_client.generate_qrcode()

    async def check_qr_status(self, qrcode_key: str) -> QRCheckResult:
        return await bilibili_client.poll_qrcode_status(qrcode_key)

    async def fetch_collections(self) -> list[PlatformCollection]:
        folders = await bilibili_client.get_user_favorites()
        result: list[PlatformCollection] = []
        for f in folders:
            folder_id = str(f.get("id", ""))
            result.append(
                PlatformCollection(
                    platform="bilibili",
                    remote_collection_id=folder_id,
                    title=f.get("title", ""),
                    item_count=int(f.get("media_count") or 0),
                    cover_url="",
                )
            )
        return result

    async def fetch_collection_items(
        self,
        remote_collection_id: str,
        cursor: int = 0,
        page_size: int = 20,
    ) -> tuple[list[PlatformItem], bool, int]:
        # B 站页码从 1 开始，cursor 为已经获取的页数（0 表示第 1 页）
        pn = cursor + 1
        data = await bilibili_client.get_favorite_content(
            media_id=remote_collection_id, pn=pn, ps=page_size
        )
        medias = data.get("medias") or []
        has_more = data.get("has_more", False)
        next_cursor = pn

        items: list[PlatformItem] = []
        for m in medias:
            bvid = m.get("bvid") or ""
            if not bvid:
                continue
            author_name = (m.get("upper") or {}).get("name", "")
            items.append(
                PlatformItem(
                    platform="bilibili",
                    remote_item_id=bvid,
                    title=m.get("title", ""),
                    author=author_name,
                    duration=int(m.get("duration") or 0),
                    cover_url=m.get("cover", ""),
                    canonical_url=f"https://www.bilibili.com/video/{bvid}",
                    item_type="video",
                )
            )
        return items, has_more, next_cursor

    async def fetch_item_content(
        self,
        remote_item_id: str,
        part_id: int = 0,
        item_meta: Optional[PlatformItem] = None,
    ) -> str:
        """
        获取 B 站实质视频正文 (字幕优先 -> DASH 音频 ASR)
        若指定了 part_id 则针对该 cid 提取，否则拉取分P信息默认第一P
        """
        title = item_meta.title if item_meta else ""
        cid = part_id
        if cid <= 0:
            pages = await bilibili_content_fetcher.fetch_video_pages(remote_item_id)
            if not pages:
                raise RuntimeError(f"未能解析到视频的分P信息 [{remote_item_id}]")
            cid = pages[0]["cid"]

        return await bilibili_content_fetcher.fetch_transcript(
            bvid=remote_item_id, cid=cid, title=title
        )
