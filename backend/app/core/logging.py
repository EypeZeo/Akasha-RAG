"""
日志配置模块

基于 loguru 的统一日志管理。
支持动态实时调整控制台输出日志级别（DEBUG, INFO, WARNING, ERROR, CRITICAL, NONE），
并持久化至 logs/log_config.json 供下次启动自动应用。
全量及 ERROR 日志持久化至本地固定 logs/ 目录。
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from loguru import logger

VALID_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "NONE"]

# 前端高频轮询接口：只静默其成功访问日志（200/304），4xx/5xx 一律照打
_QUIET_ACCESS_PREFIXES = (
    "/api/knowledge/sync/",
    "/api/auth/douyin/login/status",
    "/api/system/audio-cache-stats",
    "/api/knowledge/export/batch/",
)


class _QuietPollingAccessFilter(logging.Filter):
    """丢弃高频轮询接口的成功 uvicorn 访问日志；错误状态码保留。"""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            args = record.args
            # uvicorn.access 正常是 (client, method, path, http_version, status)，
            # 但反代 / WebSocket 握手等场景格式不同，需防御性判断。
            if isinstance(args, tuple) and len(args) >= 5:
                path, status = args[2], args[4]
                if status in (200, 304) and any(
                    str(path).startswith(p) for p in _QUIET_ACCESS_PREFIXES
                ):
                    return False
        except Exception:
            pass
        return True

# 定位统一 logs 目录（若在 backend 内运行则向上寻找项目根目录 logs，或在当前目录创建 logs）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
LOG_DIR = PROJECT_ROOT / "logs"
CONFIG_FILE = LOG_DIR / "log_config.json"


class _ApplicationLogHandler(logging.Handler):
    """Route service stdlib logs to the same console and rotating file sinks."""

    def emit(self, record: logging.LogRecord) -> None:
        def origin(log_record):
            log_record.update(name=record.name, function=record.funcName, line=record.lineno)

        logger.patch(origin).opt(exception=record.exc_info).log(record.levelname, record.getMessage())


class LogManager:
    """动态日志管理器"""

    def __init__(self) -> None:
        self.console_handler_id: Optional[int] = None
        self.file_all_handler_id: Optional[int] = None
        self.file_error_handler_id: Optional[int] = None
        self.current_level: str = "INFO"

    def _ensure_dir(self) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)

    def _load_saved_level(self) -> str:
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    saved = data.get("level", "INFO").upper()
                    if saved in VALID_LEVELS:
                        return saved
            except Exception:
                pass
        return "INFO"

    def _save_level(self, level: str) -> None:
        self._ensure_dir()
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump({"level": level}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("保存日志配置文件失败: {}", e)

    def get_level(self) -> str:
        return self.current_level

    def set_level(self, level_name: str, persist: bool = True) -> bool:
        """
        动态调整控制台日志输出级别（即刻生效，无需重启服务）

        :param level_name: DEBUG | INFO | WARNING | ERROR | CRITICAL | NONE
        :param persist: 是否写入配置文件下次继续生效
        """
        level_upper = level_name.upper()
        if level_upper not in VALID_LEVELS:
            return False

        self.current_level = level_upper

        # 移除现有控制台 handler
        if self.console_handler_id is not None:
            try:
                logger.remove(self.console_handler_id)
            except Exception:
                pass
            self.console_handler_id = None

        # 若不是 NONE，则添加新的控制台 handler
        if level_upper != "NONE":
            self.console_handler_id = logger.add(
                sys.stderr,
                format=(
                    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
                    "<level>{level: <8}</level> | "
                    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
                    "<level>{message}</level>"
                ),
                level=level_upper,
                colorize=sys.stderr.isatty(),
                diagnose=False,
            )

        if persist:
            self._save_level(level_upper)

        logger.info("控制台日志级别已动态调整为: {}", level_upper)
        return True

    def init_logging(self) -> None:
        """初始化日志配置"""
        self._ensure_dir()
        logger.remove()
        self.console_handler_id = None

        # 恢复保存的级别
        initial_level = self._load_saved_level()
        self.current_level = initial_level

        # 添加控制台 handler
        if initial_level != "NONE":
            self.console_handler_id = logger.add(
                sys.stderr,
                format=(
                    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
                    "<level>{level: <8}</level> | "
                    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
                    "<level>{message}</level>"
                ),
                level=initial_level,
                colorize=sys.stderr.isatty(),
                diagnose=False,
            )

        # 文件日志 — 全量 (DEBUG 以上)
        all_log_path = LOG_DIR / "all_{time:YYYY-MM-DD}.log"
        self.file_all_handler_id = logger.add(
            str(all_log_path),
            rotation="00:00",
            retention="7 days",
            encoding="utf-8",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}",
            level="DEBUG",
            diagnose=False,
            enqueue=True,
        )

        # 文件日志 — 仅 ERROR (错误归档追溯)
        error_log_path = LOG_DIR / "error_{time:YYYY-MM-DD}.log"
        self.file_error_handler_id = logger.add(
            str(error_log_path),
            rotation="00:00",
            retention="30 days",
            encoding="utf-8",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}",
            level="ERROR",
            diagnose=False,
            enqueue=True,
        )

        application_logger = logging.getLogger("app")
        for handler in list(application_logger.handlers):
            if isinstance(handler, _ApplicationLogHandler):
                application_logger.removeHandler(handler)
        application_logger.addHandler(_ApplicationLogHandler())
        application_logger.setLevel(logging.DEBUG)
        application_logger.propagate = False

        # 静默前端高频轮询接口的成功访问日志（可用 ACCESS_LOG_QUIET=false 关闭）
        try:
            from app.core.config import settings as _settings

            access_logger = logging.getLogger("uvicorn.access")
            access_logger.filters = [
                f for f in access_logger.filters
                if not isinstance(f, _QuietPollingAccessFilter)
            ]
            if getattr(_settings, "access_log_quiet", True):
                access_logger.addFilter(_QuietPollingAccessFilter())
        except Exception:
            pass

        logger.info("日志系统初始化完成 (当前控制台级别: {})，日志存储目录: {}", initial_level, LOG_DIR)


log_manager = LogManager()


def setup_logging() -> None:
    """对外兼容的初始化函数"""
    log_manager.init_logging()
