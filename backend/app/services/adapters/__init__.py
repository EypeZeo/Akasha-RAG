"""
平台适配器包
"""
from app.services.adapters.base import (
    AuthStatus,
    BasePlatformAdapter,
    PlatformCollection,
    PlatformItem,
    PlatformItemPart,
    QRCodeInfo,
    QRCheckResult,
)
from app.services.adapters.factory import get_adapter, list_supported_platforms

__all__ = [
    "AuthStatus",
    "BasePlatformAdapter",
    "PlatformCollection",
    "PlatformItem",
    "PlatformItemPart",
    "QRCodeInfo",
    "QRCheckResult",
    "get_adapter",
    "list_supported_platforms",
]
