"""
后台任务工作线程

使用独立线程 + 队列处理入库任务，避免阻塞 API 请求。
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = ("done", "failed", "cancelled")


class Worker:
    """
    后台任务工作器

    在独立线程中串行执行任务，支持：
    - 提交任务并获取 task_id
    - 查询任务进度和状态
    - 取消进行中的任务
    """

    def __init__(self, max_queue_size: int = 100) -> None:
        """
        初始化工作器

        启动后台线程监听任务队列。
        
        :param max_queue_size: 队列最大容量，防止无限堆积
        """
        self._queue: queue.Queue = queue.Queue(maxsize=max_queue_size)
        self._thread: threading.Thread = threading.Thread(
            target=self._run, daemon=True, name="knowledge-worker"
        )
        self._current_task_id: Optional[str] = None
        self._tasks: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._running = False
        # A provider logout must not cancel unrelated providers in a mixed batch.
        self._blocked_platforms: set[str] = set()

    def start(self) -> None:
        """
        启动工作线程
        """
        if self._thread.is_alive():
            return
        self._running = True
        if self._thread._started.is_set():
            self._thread = threading.Thread(
                target=self._run, daemon=True, name="knowledge-worker"
            )
        self._thread.start()
        logger.info("后台工作线程已启动")

    def stop(self) -> None:
        """
        停止工作线程
        """
        self._running = False

    def submit(
        self,
        task_id: str,
        func: Callable,
        *args,
        progress_total: int = 0,
        progress_message: str = "等待处理...",
        timeout: float = 5.0,
        **kwargs,
    ) -> None:
        """
        提交任务到队列

        :param task_id: 任务唯一标识
        :param func: 要执行的可调用对象
        :param args: 位置参数
        :param progress_total: 进度总数
        :param progress_message: 进度消息
        :param timeout: 队列满时等待超时（秒）
        :param kwargs: 关键字参数
        :raises queue.Full: 队列已满且超时
        """
        self._purge_stale_tasks()
        with self._lock:
            self._tasks[task_id] = {
                "status": "queued",
                "progress": 0,
                "total": progress_total,
                "message": progress_message,
                "cancelled": False,
                "created_at": time.time(),
            }
        try:
            self._queue.put((task_id, func, args, kwargs), timeout=timeout)
            logger.info("任务已提交: %s", task_id)
        except queue.Full:
            with self._lock:
                self._tasks[task_id]["status"] = "failed"
                self._tasks[task_id]["message"] = "任务队列已满，请稍后重试"
            logger.error("任务队列已满，拒绝任务: %s", task_id)
            raise RuntimeError("任务队列已满，请稍后重试")

    def get_progress(self, task_id: str) -> Optional[dict]:
        """
        查询任务进度

        :param task_id: 任务 ID
        :return: 进度信息字典，不存在则返回 None
        """
        with self._lock:
            task = self._tasks.get(task_id)
            return dict(task) if task is not None else None

    def cancel(self, task_id: str) -> bool:
        """请求取消一个排队中/执行中的任务；执行体需自行检查 is_cancelled 并停下。"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task["status"] in ("done", "failed", "cancelled"):
                return False
            task["cancelled"] = True
            task["message"] = "正在取消..."
            return True

    def is_cancelled(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            return bool(task and task.get("cancelled"))

    def block_platform(self, platform: str) -> None:
        """Reject new work for a provider and stop existing work at checkpoints."""
        with self._lock:
            self._blocked_platforms.add(platform.strip().lower())

    def unblock_platform(self, platform: str) -> None:
        """Allow work again after a fresh successful login."""
        with self._lock:
            self._blocked_platforms.discard(platform.strip().lower())

    def is_platform_blocked(self, platform: str | None) -> bool:
        if not platform:
            return False
        with self._lock:
            return platform.strip().lower() in self._blocked_platforms

    def blocked_platforms(self) -> set[str]:
        with self._lock:
            return set(self._blocked_platforms)

    def _purge_stale_tasks(self) -> None:
        """清理已到终态且超过保留期的任务记录，仍在运行/排队的任务不受影响。"""
        ttl = settings.worker_task_retention_minutes * 60
        now = time.time()
        with self._lock:
            stale = [
                tid
                for tid, task in self._tasks.items()
                if task.get("status") in _TERMINAL_STATUSES and now - task.get("created_at", now) > ttl
            ]
            for tid in stale:
                self._tasks.pop(tid, None)

    def has_active_tasks(self) -> bool:
        """包含排队任务，防止重置操作覆盖仍在执行的入库状态。"""
        with self._lock:
            return any(
                task["status"] in ("queued", "running")
                for task in self._tasks.values()
            )

    def get_current_task(self) -> Optional[str]:
        """
        获取当前正在执行的任务 ID

        :return: 任务 ID 或 None
        """
        with self._lock:
            return self._current_task_id

    def _run(self) -> None:
        """
        后台线程主循环

        从队列取任务并执行，更新状态。
        """
        while self._running:
            try:
                task_id, func, args, kwargs = self._queue.get(timeout=1)
            except queue.Empty:
                continue

            with self._lock:
                self._current_task_id = task_id
                self._tasks[task_id]["status"] = "running"
                self._tasks[task_id]["message"] = "正在处理..."

            try:
                # 执行任务
                func(*args, task_id=task_id, **kwargs)

                with self._lock:
                    if self._tasks[task_id].get("cancelled"):
                        self._tasks[task_id]["status"] = "cancelled"
                        self._tasks[task_id]["message"] = "已取消"
                    else:
                        self._tasks[task_id]["status"] = "done"
                        self._tasks[task_id]["message"] = "处理完成"
                        self._tasks[task_id]["progress"] = (
                            self._tasks[task_id]["total"]
                        )

            except Exception as exc:
                logger.exception("任务执行失败: %s", task_id)
                with self._lock:
                    self._tasks[task_id]["status"] = "failed"
                    self._tasks[task_id]["message"] = str(exc)[:500]

            finally:
                with self._lock:
                    self._current_task_id = None
                self._queue.task_done()

    def update_progress(
        self,
        task_id: str,
        progress: int,
        total: int,
        message: str = "",
    ) -> None:
        """
        由执行中的任务回调，更新进度

        :param task_id: 任务 ID
        :param progress: 当前进度
        :param total: 总数量
        :param message: 进度消息
        """
        with self._lock:
            if task_id in self._tasks and self._tasks[task_id]["status"] not in ("done", "failed"):
                self._tasks[task_id]["progress"] = progress
                self._tasks[task_id]["total"] = total
                if message:
                    self._tasks[task_id]["message"] = message


# 全局单例
worker = Worker()
