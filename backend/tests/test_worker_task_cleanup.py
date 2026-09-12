"""
PERF-11 回归测试：worker._tasks 需要和 export_worker 一样的 TTL 清理

之前没有任何清理机制，长时间运行的进程会让 _tasks 无限增长。清理只应该
清掉已到终态（done/failed/cancelled）且超过保留期的记录，仍在排队/运行
中的任务无论多"旧"都不能被清掉。
"""
import time

from app.core.config import settings
from app.services.worker import Worker


def test_purge_removes_only_stale_terminal_tasks(monkeypatch):
    monkeypatch.setattr(settings, "worker_task_retention_minutes", 1)
    worker = Worker()
    now = time.time()
    ttl_seconds = settings.worker_task_retention_minutes * 60

    worker._tasks.update({
        "old-done": {"status": "done", "created_at": now - ttl_seconds - 10},
        "old-failed": {"status": "failed", "created_at": now - ttl_seconds - 10},
        "old-cancelled": {"status": "cancelled", "created_at": now - ttl_seconds - 10},
        "old-but-running": {"status": "running", "created_at": now - ttl_seconds - 10},
        "recent-done": {"status": "done", "created_at": now},
        "queued": {"status": "queued", "created_at": now - ttl_seconds - 10},
    })

    worker._purge_stale_tasks()

    remaining = set(worker._tasks.keys())
    assert remaining == {"old-but-running", "recent-done", "queued"}


def test_submit_creates_a_timestamped_task_record():
    worker = Worker()
    worker._queue.put = lambda *a, **k: None  # avoid starting real processing
    try:
        worker.submit("t1", lambda: None)
    except Exception:
        pass
    assert "created_at" in worker._tasks["t1"]
