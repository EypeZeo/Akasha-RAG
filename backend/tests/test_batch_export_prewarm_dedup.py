"""
PERF-10 回归测试：本地单文件导出的 AI 摘要预热不该做两遍

`export_to_local_directory(pack_mode="single")` 之前自己先跑一遍
`_prewarm_ai_summaries`，然后调用 `export_batch()`——而 `export_batch()`
内部也会自己跑一遍同样的预热，等于同一批条目的 AI 整理生成函数被调用了
两次（第二次虽然命中缓存，但仍是多余的一轮循环 + 进度回调）。
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.entities import ContentItem, IngestionItem
from app.services.batch_export_service import BatchExportService


def test_single_pack_mode_prewarms_ai_summaries_exactly_once(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'export.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine)

    with factory() as db:
        for i in range(3):
            item = ContentItem(platform="douyin", remote_item_id=f"v{i}", title=f"标题{i}")
            db.add(item)
            db.flush()
            db.add(IngestionItem(content_item_id=item.id, status="done", transcript_text=f"text{i}"))
        db.commit()

        # Prewarm calls export_ai_organized(cache, db=db) (generate defaults
        # to True); the actual markdown writer calls it with generate=False
        # to only read the now-cached summary. Only count the former, so
        # this test measures prewarm invocations specifically, not every
        # call to the function.
        calls = []

        def _fake_export_ai_organized(cache, db=None, generate=True):
            if generate:
                calls.append(cache.platform_item_id)
            return "AI 摘要"

        monkeypatch.setattr(
            "app.services.batch_export_service.export_ai_organized",
            _fake_export_ai_organized,
        )

        service = BatchExportService()
        service.export_to_local_directory(
            db, target_dir=str(tmp_path / "out"), content_type="ai",
            export_format="markdown", pack_mode="single",
        )

        # 3 items, prewarmed exactly once each -- not 6 (twice each).
        assert calls == ["v0", "v1", "v2"]
