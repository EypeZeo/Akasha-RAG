"""内容形态（视频 / 图文）的唯一判据。

历史背景（本次收敛的理由）
--------------------------
仓库里曾经同时存在两条互相矛盾的判据：

- 列表口径（``knowledge_service.py``）：``duration == 0 or duration is None``
- 同步口径（``favorites_service.py``）：``duration <= 0``

``content_items.duration`` 是 ``NOT NULL DEFAULT 0``（models/entities.py:110），
因此一个负时长条目会被列表口径判成「视频」、被同步口径判成「图文」——
同一个条目在同一个页面上的两个数字互相打架。

现在统一为 ``duration <= 0``（含 NULL），与 ``favorites_service.py`` 文档化的
全局口径一致：「duration > 0 为视频，duration == 0 / NULL 为图文」。

平台参数
--------
``platform`` 只为签名对称而保留：当前注册的每一个平台（douyin / bilibili）的形态
都由时长推导，这一点被
``tests/test_favorites_service.py::test_count_videos_by_kind_uses_duration_not_platform``
（"零时长条目一律计为图文，与平台无关"）钉死。未来若出现「恒为视频」的平台，
拦截点在平台事实层（``platform_registry.PlatformFacts.content_kind_by_duration``），
而不是在这里堆 ``if platform == ...``。
"""
from __future__ import annotations

from sqlalchemy import ColumnElement, or_

NOTE = "note"
VIDEO = "video"


def is_note(platform: str | None, duration: int | None) -> bool:
    """``True`` = 图文（note），``False`` = 视频（video）。duration 为 ``None`` 按图文处理。"""
    return duration is None or duration <= 0


def content_kind_for(platform: str | None, duration: int | None) -> str:
    """返回 ``content_items.content_kind`` 的合法取值：``"note"`` / ``"video"``。"""
    return NOTE if is_note(platform, duration) else VIDEO


def note_predicate(column: ColumnElement) -> ColumnElement:
    """与 :func:`is_note` 等价的 SQL 判据。

    SQL 三值逻辑下 ``NULL <= 0`` 的结果是 NULL 而不是 TRUE，所以必须显式补上
    ``IS NULL``，否则 NULL 行既不算视频也不算图文——而 ``duration`` 列在遗留库
    里可能是 NULL。
    """
    return or_(column <= 0, column.is_(None))


def video_predicate(column: ColumnElement) -> ColumnElement:
    """:func:`note_predicate` 的精确补集，保证 ``video + note == total``。"""
    return column > 0
