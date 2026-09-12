"""
抖音平台适配器实现
"""
from __future__ import annotations

import logging
from typing import Optional

from app.services.adapters.base import (
    AuthStatus,
    BasePlatformAdapter,
    PlatformCollection,
    PlatformItem,
    QRCodeInfo,
    QRCheckResult,
)
from app.services.asr_service import asr_service
from app.services.douyin_collector import collector
from app.services.media_service import audio_cache_lease, download_audio
from app.services.text_processing import clean_title_for_index
from app.services.vision_service import vision_service

logger = logging.getLogger(__name__)


class DouyinAdapter(BasePlatformAdapter):
    """抖音平台适配器实现"""

    @property
    def platform_name(self) -> str:
        return "douyin"

    async def check_auth(self) -> AuthStatus:
        status = collector.check_login_status()
        return AuthStatus(
            platform="douyin",
            is_logged_in=status.get("logged_in", False),
            account_id=status.get("user_id", ""),
            nickname=status.get("nickname", ""),
            avatar_url=status.get("avatar_url", ""),
            error_message=status.get("error", ""),
        )

    async def get_qr_code(self) -> QRCodeInfo:
        qr = collector.get_login_qrcode()
        return QRCodeInfo(
            platform="douyin",
            qrcode_key=qr.get("qrcode_key", ""),
            qrcode_url=qr.get("qrcode_url", ""),
            qrcode_image_base64=qr.get("qrcode_image_base64", ""),
            expires_in=qr.get("expires_in", 180),
        )

    async def check_qr_status(self, qrcode_key: str) -> QRCheckResult:
        res = collector.check_qrcode_status(qrcode_key)
        return QRCheckResult(
            platform="douyin",
            status=res.get("status", "error"),
            message=res.get("message", ""),
            account_id=res.get("user_id", ""),
            nickname=res.get("nickname", ""),
            avatar_url=res.get("avatar_url", ""),
        )

    async def fetch_collections(self) -> list[PlatformCollection]:
        folders = collector.fetch_favorite_folders()
        result: list[PlatformCollection] = []
        for f in folders:
            result.append(
                PlatformCollection(
                    platform="douyin",
                    remote_collection_id=f.get("folder_id", ""),
                    title=f.get("title", ""),
                    item_count=f.get("count", 0),
                    cover_url=f.get("cover_url", ""),
                )
            )
        return result

    async def fetch_collection_items(
        self,
        remote_collection_id: str,
        cursor: int = 0,
        page_size: int = 20,
    ) -> tuple[list[PlatformItem], bool, int]:
        snapshot = collector.fetch_favorite_videos(
            remote_collection_id, cursor=cursor, count=page_size
        )
        items: list[PlatformItem] = []
        for v in snapshot.videos:
            dur = v.get("duration", 0)
            is_note = dur == 0 or dur is None
            items.append(
                PlatformItem(
                    platform="douyin",
                    remote_item_id=v.get("aweme_id", ""),
                    title=v.get("title", ""),
                    author=v.get("author", ""),
                    duration=dur or 0,
                    cover_url=v.get("cover_url", ""),
                    canonical_url=f"https://www.douyin.com/video/{v.get('aweme_id')}",
                    item_type="note" if is_note else "video",
                )
            )
        return items, snapshot.has_more, snapshot.cursor

    async def fetch_item_content(
        self,
        remote_item_id: str,
        part_id: int = 0,
        item_meta: Optional[PlatformItem] = None,
    ) -> str:
        """获取抖音实质正文 (图文 OCR 或 视频 ASR)"""
        is_note = item_meta is not None and item_meta.item_type == "note"
        title = item_meta.title if item_meta else ""
        cover_url = item_meta.cover_url if item_meta else ""

        if is_note:
            img_urls = vision_service.fetch_note_image_urls(remote_item_id, cover_url)
            extracted_text = ""
            if img_urls:
                extracted_text = vision_service.extract_text_from_images(img_urls, title)
            clean_title = clean_title_for_index(title)
            if extracted_text and len(extracted_text.strip()) >= 10:
                return f"【图文笔记全文】标题与文案：{clean_title}\n\n{extracted_text}"
            elif len(clean_title) >= 20:
                return f"【图文笔记全文】标题与正文：\n{clean_title}"
            raise RuntimeError("未能提取到图文正文内容")

        # 视频: 下载音频并 ASR
        with audio_cache_lease(remote_item_id):
            audio_path = download_audio(
                f"https://www.douyin.com/video/{remote_item_id}",
                remote_item_id,
            )
            if not audio_path or not audio_path.is_file():
                raise RuntimeError("音频下载未生成可用文件")
            try:
                transcript_text = asr_service.transcribe_to_text(audio_path)
                if not transcript_text or len(transcript_text.strip()) < 10:
                    raise RuntimeError("ASR 未能提取到实质音频正文")
                return transcript_text
            finally:
                audio_path.unlink(missing_ok=True)
