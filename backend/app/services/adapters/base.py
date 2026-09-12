"""
多平台适配器基础接口与数据结构

定义统一的跨平台协议，支持 Douyin、Bilibili 及后续平台 (如 Zhihu)。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AuthStatus:
    """平台鉴权登录状态"""
    platform: str
    is_logged_in: bool
    account_id: str = ""
    nickname: str = ""
    avatar_url: str = ""
    error_message: str = ""


@dataclass
class QRCodeInfo:
    """登录二维码信息"""
    platform: str
    qrcode_key: str
    qrcode_url: str
    qrcode_image_base64: str
    expires_in: int = 180


@dataclass
class QRCheckResult:
    """二维码状态轮询结果"""
    platform: str
    status: str  # "waiting" | "scanned" | "confirmed" | "expired" | "error"
    message: str
    account_id: str = ""
    nickname: str = ""
    avatar_url: str = ""


@dataclass
class PlatformCollection:
    """统一收藏夹数据结构"""
    platform: str
    remote_collection_id: str
    title: str
    item_count: int = 0
    cover_url: str = ""


@dataclass
class PlatformItemPart:
    """分P/分集元数据"""
    part_id: int
    part_index: int
    title: str
    duration: int = 0


@dataclass
class PlatformItem:
    """统一内容项元数据"""
    platform: str
    remote_item_id: str
    title: str
    author: str = ""
    duration: int = 0
    cover_url: str = ""
    canonical_url: str = ""
    item_type: str = "video"  # "video" | "note"
    parts: list[PlatformItemPart] = field(default_factory=list)


class BasePlatformAdapter(ABC):
    """跨平台适配器抽象基类"""

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """平台标识名称 (如 'douyin', 'bilibili')"""
        ...

    @abstractmethod
    async def check_auth(self) -> AuthStatus:
        """检查登录状态"""
        ...

    @abstractmethod
    async def get_qr_code(self) -> QRCodeInfo:
        """生成登录二维码"""
        ...

    @abstractmethod
    async def check_qr_status(self, qrcode_key: str) -> QRCheckResult:
        """检查扫码状态"""
        ...

    @abstractmethod
    async def fetch_collections(self) -> list[PlatformCollection]:
        """拉取用户的所有收藏夹"""
        ...

    @abstractmethod
    async def fetch_collection_items(
        self,
        remote_collection_id: str,
        cursor: int = 0,
        page_size: int = 20,
    ) -> tuple[list[PlatformItem], bool, int]:
        """
        分批拉取指定收藏夹中的内容

        :return: (items, has_more, next_cursor)
        """
        ...

    @abstractmethod
    async def fetch_item_content(
        self,
        remote_item_id: str,
        part_id: int = 0,
        item_meta: Optional[PlatformItem] = None,
    ) -> str:
        """
        获取实质正文（字幕、音频 ASR 或图文 OCR）
        拒绝非实质性内容兜底（文本低于阈值需抛出明确异常）。
        """
        ...
