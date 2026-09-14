"""
PR2C-1 回归测试：导出链接/作者字段的共享兜底逻辑

`display_link`/`display_author` 是 `batch_export_service.py`（6 条批量
导出路径）与 `markdown_export.py`（单条导出）共用的唯一兜底入口——锁死
这两个函数本身，就不需要指望每一条导出路径各自的集成测试都记得覆盖
"链接/作者缺失"这个边界。
"""
from types import SimpleNamespace

import pytest

from app.services.export_common import (
    MISSING_LINK_PLACEHOLDER,
    UNKNOWN_AUTHOR_PLACEHOLDER,
    display_author,
    display_link,
)


@pytest.mark.parametrize("content_item", [
    None,
    SimpleNamespace(canonical_url=""),
    SimpleNamespace(canonical_url=None),
])
def test_display_link_falls_back_to_placeholder_when_missing(content_item):
    assert display_link(content_item) == MISSING_LINK_PLACEHOLDER


def test_display_link_returns_the_real_url_when_present():
    content_item = SimpleNamespace(canonical_url="https://www.bilibili.com/video/BV1xx")
    assert display_link(content_item) == "https://www.bilibili.com/video/BV1xx"


@pytest.mark.parametrize("content_item", [
    None,
    SimpleNamespace(author=""),
    SimpleNamespace(author=None),
])
def test_display_author_falls_back_to_placeholder_when_missing(content_item):
    assert display_author(content_item) == UNKNOWN_AUTHOR_PLACEHOLDER


def test_display_author_returns_the_real_name_when_present():
    content_item = SimpleNamespace(author="某UP主")
    assert display_author(content_item) == "某UP主"
