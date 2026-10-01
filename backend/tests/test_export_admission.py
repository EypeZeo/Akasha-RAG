"""Bound export memory, and never expire work while it is executing."""
import asyncio
import os
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app.api.routes import knowledge
from app.services import export_worker


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(export_worker, "_tasks", {})
    monkeypatch.setattr(export_worker, "_EXPORT_TMP_DIR", tmp_path)
    executor = Mock()
    monkeypatch.setattr(export_worker, "_get_executor", lambda: executor)
    return executor, tmp_path


def test_active_exports_and_artifacts_survive_retention(isolated, monkeypatch):
    executor, tmp_path = isolated
    monkeypatch.setattr(export_worker.time, "time", lambda: 10000)
    export_worker._tasks.update({
        "running": {"status": "running", "created_at": 0},
        "queued": {"status": "queued", "created_at": 0},
        "recent": {"status": "done", "created_at": 0, "finished_at": 9999},
        "expired": {"status": "failed", "created_at": 0, "finished_at": 0},
    })
    artifact = tmp_path / "running.md"
    artifact.write_text("partial")
    os.utime(artifact, (0, 0))
    export_worker.submit_export({})
    assert "expired" not in export_worker._tasks
    assert {"running", "queued", "recent"} <= export_worker._tasks.keys()
    assert artifact.is_file()
    executor.submit.assert_called_once()


def test_queue_admission_is_bounded_without_losing_existing_jobs(isolated):
    executor, _ = isolated
    ids = [export_worker.submit_export({}) for _ in range(export_worker._MAX_ACTIVE_EXPORTS)]
    with pytest.raises(export_worker.ExportQueueFullError):
        export_worker.submit_export({"selected_ids": ["large payload"] * 10000})
    assert set(export_worker._tasks) == set(ids)
    assert executor.submit.call_count == export_worker._MAX_ACTIVE_EXPORTS


def test_executor_failure_does_not_leave_a_phantom_queued_task(isolated):
    executor, _ = isolated
    executor.submit.side_effect = RuntimeError("executor closed")
    with pytest.raises(RuntimeError, match="executor closed"):
        export_worker.submit_export({})
    assert export_worker._tasks == {}


def test_full_export_queue_returns_429(monkeypatch):
    monkeypatch.setattr(knowledge.batch_export_service, "validate_scope", Mock())
    monkeypatch.setattr(export_worker, "submit_export", Mock(side_effect=export_worker.ExportQueueFullError("full")))
    with pytest.raises(HTTPException) as error:
        asyncio.run(knowledge.export_batch_knowledge(knowledge.BatchExportRequest(), Mock()))
    assert error.value.status_code == 429
