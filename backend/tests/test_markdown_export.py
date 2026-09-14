"""
PR2C-1 回归测试：单条 Markdown 导出（`export_original`/`export_ai_organized`）
的作者/链接字段

之前两处都用 `video.platform_item_id`（纯 ID 字符串）同时冒充作者和拼
`douyin.com` 链接——对 B 站内容一定是错的（域名错、作者字段其实是个 ID
不是人名）。修复后改读 `video.content_item.author`/`.canonical_url`，
和批量导出路径共用同一个 `display_link`/`display_author` 兜底策略。
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.entities import ContentItem, IngestionItem
from app.services.markdown_export import export_ai_organized, export_original


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    return factory()


def _seed(db, canonical_url: str, author: str, transcript_text: str = "这是转写正文。"):
    item = ContentItem(
        platform="bilibili", remote_item_id="BV1xx", title="B站视频标题",
        author=author, canonical_url=canonical_url,
    )
    db.add(item)
    db.flush()
    ingestion = IngestionItem(content_item_id=item.id, status="done", transcript_text=transcript_text)
    db.add(ingestion)
    db.commit()
    return ingestion


def test_export_original_uses_real_author_and_bilibili_link():
    db = _db()
    video = _seed(db, "https://www.bilibili.com/video/BV1xx", "某UP主")
    md = export_original(video)
    assert "> 作者：某UP主" in md
    assert "> 链接：https://www.bilibili.com/video/BV1xx" in md
    assert "douyin.com" not in md


def test_export_original_shows_placeholders_when_author_and_link_missing():
    db = _db()
    video = _seed(db, "", "")
    md = export_original(video)
    assert "> 作者：未知" in md
    assert "> 链接：（链接缺失）" in md
    assert "douyin.com" not in md


def test_export_ai_organized_uses_real_author_and_bilibili_link():
    db = _db()
    video = _seed(db, "https://www.bilibili.com/video/BV1xx", "某UP主")
    md = export_ai_organized(video, generate=False)
    assert "> 作者：某UP主" in md
    assert "> 链接：https://www.bilibili.com/video/BV1xx" in md
    assert "douyin.com" not in md
