"""导出后台任务器：生命周期、进度回调、产物落盘、失败回传。"""
import io
import time

import pytest

from app.services import export_worker


def _wait_done(task_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        p = export_worker.get_progress(task_id)
        if p and p["status"] in ("done", "failed"):
            return p
        time.sleep(0.02)
    raise AssertionError("导出任务未在超时内结束")


def test_browser_mode_writes_artifact_and_reports_progress(monkeypatch):
    seen = []

    def fake_export_batch(*, db, progress_cb=None, **kwargs):
        assert db is not None  # 工作线程自建的会话
        if progress_cb:
            progress_cb(1, 2, "整理中")
            progress_cb(2, 2, "生成文件")
        seen.append(kwargs["export_format"])
        return io.BytesIO(b"hello"), "Akasha-RAG_Export.md", "text/markdown"

    monkeypatch.setattr(
        "app.services.batch_export_service.batch_export_service.export_batch",
        fake_export_batch,
    )

    task_id = export_worker.submit_export({
        "content_type": "both", "format": "markdown", "pack_mode": "single",
        "target_dir": None,
    })
    progress = _wait_done(task_id)
    assert progress["status"] == "done"
    assert progress["result"]["download_name"] == "Akasha-RAG_Export.md"

    taken = export_worker.take_download(task_id)
    assert taken is not None
    path, name, mime = taken
    assert path.read_bytes() == b"hello"
    assert name == "Akasha-RAG_Export.md"


def test_failure_is_reported_not_raised(monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("导出炸了")

    monkeypatch.setattr(
        "app.services.batch_export_service.batch_export_service.export_batch", boom
    )
    task_id = export_worker.submit_export({"format": "markdown", "target_dir": None})
    progress = _wait_done(task_id)
    assert progress["status"] == "failed"
    assert "导出炸了" in progress["message"]
    assert export_worker.take_download(task_id) is None
