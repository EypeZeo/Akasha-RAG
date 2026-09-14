"""
PR2C-1 回归测试：PDF 导出必须转义用户内容，且链接同样来自 canonical_url

`_export_pdf` 用 reportlab 的 `platypus.Paragraph`，其输入被当成一段
mini-XML 标记语言解析（`<b>`/`<br/>` 等），不是纯文本。标题、作者、链接、
AI 整理正文、原始转写正文全部是用户/模型生成的自由文本，转义前遇到
`<`、`&` 或形如 `<b>` 的子串要么让 reportlab 解析报错，要么被误解析成
标记。两层断言都要有：(a) 转义后拼进 Paragraph 的文本确实是转义结果；
(b) 不 mock Paragraph，真的生成一次 PDF，证明 reportlab 真的能接受这些
输入而不是只证明"调用参数变了"。
"""
from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import Mock

import reportlab.platypus as platypus

from app.services.batch_export_service import BatchExportService


def _fake_item(canonical_url: str, title: str, transcript_text: str, author: str = "某UP主"):
    cache = SimpleNamespace(
        platform_item_id="BV1xx",
        title=title,
        updated_at=None,
        transcript_text=transcript_text,
    )
    fv = SimpleNamespace(author=author, canonical_url=canonical_url)
    return cache, fv


def test_pdf_safe_escapes_special_characters():
    service = BatchExportService()
    assert service._pdf_safe("<b>加粗</b> & 一些 <标签>") == "&lt;b&gt;加粗&lt;/b&gt; &amp; 一些 &lt;标签&gt;"
    assert service._pdf_safe(None) == ""
    assert service._pdf_safe(123) == "123"


def test_paragraph_receives_escaped_text_not_raw_input(monkeypatch):
    recorded: list[str] = []

    class _RecordingParagraph:
        def __init__(self, text, style):
            recorded.append(text)

    monkeypatch.setattr(platypus, "Paragraph", _RecordingParagraph)
    # SimpleDocTemplate.build would try to actually lay out these fake
    # flowables; skip it here since this test only cares about what text
    # reached Paragraph(), not the final document.
    monkeypatch.setattr(platypus.SimpleDocTemplate, "build", Mock())

    service = BatchExportService()
    item = _fake_item(
        "https://www.bilibili.com/video/BV1xx",
        title="标题<b>加粗</b>",
        transcript_text="转写正文包含 & 和 <script>alert(1)</script>",
    )
    service._export_pdf([item], content_type="original")

    joined = "\n".join(recorded)
    assert "&lt;b&gt;加粗&lt;/b&gt;" in joined
    assert "&lt;script&gt;" in joined
    assert "<script>" not in joined
    assert "<b>加粗</b>" not in joined


def test_export_pdf_end_to_end_with_hostile_input_produces_a_real_pdf():
    """不 mock Paragraph，真的跑一次 _export_pdf 生成 PDF 二进制——转义前
    这类输入会让 reportlab 因为不完整的标记语法直接抛异常。"""
    service = BatchExportService()
    item = _fake_item(
        "https://www.bilibili.com/video/BV1xx",
        title="标题 & 特殊字符 <未闭合标签",
        transcript_text="第一行包含 <b>加粗</b>\n第二行包含 & 符号\n第三行 <script>危险</script>",
    )
    buf = service._export_pdf([item], content_type="original")
    assert isinstance(buf, io.BytesIO)
    data = buf.getvalue()
    assert len(data) > 0
    assert data[:4] == b"%PDF"


def test_export_pdf_link_uses_canonical_url_not_douyin(monkeypatch):
    recorded: list[str] = []

    class _RecordingParagraph:
        def __init__(self, text, style):
            recorded.append(text)

    monkeypatch.setattr(platypus, "Paragraph", _RecordingParagraph)
    monkeypatch.setattr(platypus.SimpleDocTemplate, "build", Mock())

    service = BatchExportService()
    item = _fake_item("https://www.bilibili.com/video/BV1xx", title="标题", transcript_text="正文")
    service._export_pdf([item], content_type="original")

    joined = "\n".join(recorded)
    assert "bilibili.com" in joined
    assert "douyin.com" not in joined
