"""
批量导出后台任务器

导出（尤其 content_type=both 时逐条调用 LLM 生成 AI 整理）耗时可达数分钟，
放在 FastAPI 请求里会阻塞事件循环、前端只能干等。这里用一个独立的单线程池
承接导出任务，路由提交后立即返回 task_id，前端轮询进度。

关键约束：
- 不复用 app.services.worker（那是入库队列，复用会互相阻塞）。
- 绝不接收路由的 request-scoped Session；工作线程内部用 session_factory() 自建会话。
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from app.core.config import settings
from app.db.session import session_factory

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="export")
_tasks: dict[str, dict] = {}
_lock = threading.Lock()

_EXPORT_TMP_DIR = Path(__file__).resolve().parents[2] / "app" / "storage" / "export_tmp"


def _export_tmp_dir() -> Path:
    _EXPORT_TMP_DIR.mkdir(parents=True, exist_ok=True)
    return _EXPORT_TMP_DIR


def get_progress(task_id: str) -> Optional[dict]:
    with _lock:
        task = _tasks.get(task_id)
        return dict(task) if task is not None else None


def update_progress(task_id: str, progress: int, total: int, message: str = "") -> None:
    with _lock:
        task = _tasks.get(task_id)
        if task and task["status"] not in ("done", "failed"):
            task["progress"] = progress
            task["total"] = total
            if message:
                task["message"] = message


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    with _lock:
        if _executor._shutdown:
            _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="export")
        return _executor


def submit_export(params: dict) -> str:
    """登记并提交一个导出任务，返回 task_id。params 必须是纯数据（无 Session）。"""
    _purge_stale()
    task_id = uuid.uuid4().hex[:12]
    mode = "local" if (params.get("target_dir") or "").strip() else "browser"
    with _lock:
        _tasks[task_id] = {
            "status": "queued",
            "progress": 0,
            "total": 0,
            "message": "排队中...",
            "mode": mode,
            "result": None,
            "created_at": time.time(),
        }
    _get_executor().submit(_run, task_id, params)
    logger.info("导出任务已提交: %s (mode=%s)", task_id, mode)
    return task_id


def _run(task_id: str, params: dict) -> None:
    with _lock:
        _tasks[task_id]["status"] = "running"
        _tasks[task_id]["message"] = "正在准备导出..."
    try:
        from app.services.batch_export_service import batch_export_service

        def cb(done: int, total: int, msg: str) -> None:
            update_progress(task_id, done, total, msg)

        # 工作线程独占的会话，跟随 with 块创建/提交/销毁
        with session_factory() as db:
            if (params.get("target_dir") or "").strip():
                result = batch_export_service.export_to_local_directory(
                    db=db,
                    collection_id=params.get("collection_id"),
                    selected_ids=params.get("selected_ids"),
                    content_type=params.get("content_type", "both"),
                    export_format=params.get("format", "markdown"),
                    pack_mode=params.get("pack_mode", "single"),
                    target_dir=params["target_dir"],
                    auto_open=params.get("auto_open", True),
                    progress_cb=cb,
                )
            else:
                buffer, filename, mime = batch_export_service.export_batch(
                    db=db,
                    collection_id=params.get("collection_id"),
                    selected_ids=params.get("selected_ids"),
                    content_type=params.get("content_type", "both"),
                    export_format=params.get("format", "markdown"),
                    pack_mode=params.get("pack_mode", "single"),
                    progress_cb=cb,
                )
                out = _export_tmp_dir() / f"{task_id}{Path(filename).suffix}"
                out.write_bytes(buffer.getvalue())
                result = {
                    "success": True,
                    "download_name": filename,
                    "file_path": str(out),
                    "mime_type": mime,
                    "file_count": 1,
                }
            db.commit()

        with _lock:
            t = _tasks[task_id]
            t["status"] = "done"
            t["result"] = result
            t["progress"] = t["total"] or t["progress"]
            t["message"] = result.get("message") or "导出完成"
        logger.info("导出任务完成: %s", task_id)
    except Exception as exc:  # noqa: BLE001 — 需把任意失败回传前端
        logger.exception("导出任务失败: %s", task_id)
        with _lock:
            _tasks[task_id]["status"] = "failed"
            _tasks[task_id]["message"] = str(exc)[:500]
            _tasks[task_id]["result"] = {"success": False, "message": str(exc)[:500]}


def take_download(task_id: str) -> Optional[tuple[Path, str, str]]:
    """浏览器模式：返回 (产物路径, 下载名, mime)。产物不删除，留给定时器清理。"""
    task = get_progress(task_id)
    if not task or task["status"] != "done" or not task.get("result"):
        return None
    result = task["result"]
    path = Path(result.get("file_path", ""))
    if not path.is_file():
        return None
    return path, result.get("download_name", path.name), result.get("mime_type", "application/octet-stream")


def shutdown() -> None:
    """应用关闭时优雅停掉导出线程池（不等待长任务）。"""
    try:
        _executor.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass


def _purge_stale() -> None:
    """清理过期产物文件与任务记录。"""
    ttl = settings.export_tmp_retention_minutes * 60
    now = time.time()
    try:
        for f in _export_tmp_dir().iterdir():
            if f.is_file() and now - f.stat().st_mtime > ttl:
                f.unlink(missing_ok=True)
    except Exception:
        pass
    with _lock:
        stale = [tid for tid, t in _tasks.items() if now - t.get("created_at", now) > ttl]
        for tid in stale:
            _tasks.pop(tid, None)
