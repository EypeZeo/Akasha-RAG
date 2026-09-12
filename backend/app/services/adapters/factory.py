"""
平台适配器工厂与注册表
"""
from __future__ import annotations

from typing import Dict

from app.services.adapters.base import BasePlatformAdapter

_ADAPTERS: Dict[str, BasePlatformAdapter] = {}


def get_adapter(platform: str) -> BasePlatformAdapter:
    """
    根据平台标识获取对应适配器（延迟实例化，彻底避免循环导入）

    :param platform: 'douyin' | 'bilibili'
    :return: BasePlatformAdapter 实例
    :raises ValueError: 若请求未注册的平台
    """
    key = platform.lower().strip()
    if key in _ADAPTERS:
        return _ADAPTERS[key]

    if key == "douyin":
        from app.services.adapters.douyin import DouyinAdapter
        adapter = DouyinAdapter()
    elif key == "bilibili":
        from app.services.adapters.bilibili import BilibiliAdapter
        adapter = BilibiliAdapter()
    else:
        valid_platforms = "douyin, bilibili"
        raise ValueError(f"不支持的平台: '{platform}'，当前支持的平台包括: {valid_platforms}")

    _ADAPTERS[key] = adapter
    return adapter


def list_supported_platforms() -> list[str]:
    """返回当前支持的所有平台标识列表"""
    return ["douyin", "bilibili"]
