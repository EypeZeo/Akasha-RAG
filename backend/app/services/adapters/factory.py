"""
平台适配器工厂与注册表

平台白名单不在这里：唯一事实来源是 ``app/services/platform_registry.py``。
本模块只负责「已登记平台 -> 适配器实现」的分发，并保证两边不漂移。
"""
from __future__ import annotations

from typing import Dict

from app.services.adapters.base import BasePlatformAdapter
from app.services.platform_registry import UnsupportedPlatformError, get_platform, supported_platforms

_ADAPTERS: Dict[str, BasePlatformAdapter] = {}


def get_adapter(platform: str) -> BasePlatformAdapter:
    """
    根据平台标识获取对应适配器（延迟实例化，彻底避免循环导入）

    :param platform: platform_registry 中已登记的平台标识（如 'douyin' | 'bilibili'）
    :return: BasePlatformAdapter 实例
    :raises UnsupportedPlatformError: 若请求未登记的平台（ValueError 的子类）
    """
    key = platform.lower().strip()
    if key in _ADAPTERS:
        return _ADAPTERS[key]

    get_platform(key)  # 未登记平台：在这里失败，而不是落进某个 else 分支

    if key == "douyin":
        from app.services.adapters.douyin import DouyinAdapter
        adapter = DouyinAdapter()
    elif key == "bilibili":
        from app.services.adapters.bilibili import BilibiliAdapter
        adapter = BilibiliAdapter()
    elif key == "zhihu":
        from app.services.adapters.zhihu import ZhihuAdapter
        adapter = ZhihuAdapter()
    else:
        raise UnsupportedPlatformError(
            f"平台 '{key}' 已在 platform_registry 登记，但 adapters/factory 没有对应实现"
        )

    _ADAPTERS[key] = adapter
    return adapter


def list_supported_platforms() -> list[str]:
    """返回当前支持的所有平台标识列表（由平台事实注册表推导）"""
    return list(supported_platforms())
