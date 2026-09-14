"""
导出服务共享工具

`batch_export_service.py`（批量导出：Markdown/Excel/Word/PPT/PDF）与
`markdown_export.py`（单条 Markdown 导出）两个模块都需要同一套"缺失链接
时的兜底策略"——放在这里而不是任一模块里，是为了避免两者互相 import
造成循环依赖，也保证兜底文案在所有导出路径上是同一个字符串。
"""
from __future__ import annotations

MISSING_LINK_PLACEHOLDER = "（链接缺失）"
UNKNOWN_AUTHOR_PLACEHOLDER = "未知"


def display_link(content_item) -> str:
    """
    所有导出格式共用的链接兜底逻辑。

    `content_item` 是一个 `ContentItem`（或兼容的 `FavoriteVideo` 别名）
    实例，也可能是 None——canonical_url 缺失或对象本身缺失时一律返回同一
    个占位符，不允许任何调用点各自决定兜底文案，避免"改五处漏一处"。

    :param content_item: 具有 `canonical_url` 属性的对象，或 None
    :return: 真实链接，或缺失时的占位符
    """
    url = getattr(content_item, "canonical_url", None) if content_item is not None else None
    return url if url else MISSING_LINK_PLACEHOLDER


def display_author(content_item) -> str:
    """作者字段的同款兜底策略，和 display_link 用同一个占位符风格。"""
    author = getattr(content_item, "author", None) if content_item is not None else None
    return author if author else UNKNOWN_AUTHOR_PLACEHOLDER
