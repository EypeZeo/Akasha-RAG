"""
审计日志系统

记录关键操作的审计日志，用于安全审计、问题排查和合规要求。
"""
from __future__ import annotations

import datetime
import json
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from loguru import logger
from pydantic import BaseModel, Field


_SECRET_FIELDS = ("key", "token", "secret", "password", "cookie", "authorization", "credential")


def _redact_details(value: Any) -> Any:
    """Keep structured diagnostics without persisting credential values."""
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if any(marker in str(key).lower() for marker in _SECRET_FIELDS)
            else _redact_details(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_details(item) for item in value]
    return value


class AuditEventType(str, Enum):
    """审计事件类型"""
    # 认证相关
    LOGIN_START = "login_start"
    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILURE = "login_failure"
    LOGOUT = "logout"
    
    # 数据操作
    SYNC_FAVORITES = "sync_favorites"
    CLEAR_KNOWLEDGE = "clear_knowledge"
    RESET_FAILED = "reset_failed"
    BATCH_EXPORT = "batch_export"
    
    # 设置变更
    SETTINGS_CHANGE = "settings_change"
    DEVELOPER_MODE_TOGGLE = "developer_mode_toggle"
    
    # 系统操作
    CACHE_CLEAN = "cache_clean"
    LOG_LEVEL_CHANGE = "log_level_change"
    
    # 安全事件
    UNAUTHORIZED_ACCESS = "unauthorized_access"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"


class AuditEvent(BaseModel):
    """审计事件模型"""
    timestamp: datetime.datetime
    event_type: AuditEventType
    user_id: Optional[str] = None  # 未来扩展多用户时使用
    ip_address: str
    platform: Optional[str] = None  # douyin, bilibili, etc.
    action: str
    resource: Optional[str] = None
    result: str  # success, failure, error
    details: dict[str, Any] = Field(default_factory=dict)
    error_message: Optional[str] = None


class AuditLogger:
    """审计日志记录器"""
    
    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._setup_logger()
    
    def _setup_logger(self):
        """配置审计日志记录器"""
        log_file = self.log_dir / "audit.log"
        
        # Own only this sink; audit initialization must never remove the
        # application's console/error handlers or copy unrelated log records.
        if getattr(self, "_handler_id", None) is not None:
            logger.remove(self._handler_id)
        
        # 添加专用的审计日志 handler
        self._handler_id = logger.add(
            log_file,
            format="{message}",
            rotation="10 MB",
            retention="90 days",
            compression="zip",
            enqueue=True,
            filter=lambda record: record["extra"].get("akasha_audit") is True,
            diagnose=False,
        )
        self._logger = logger.bind(akasha_audit=True)
    
    def log_event(self, event: AuditEvent):
        """记录审计事件"""
        try:
            log_entry = {
                "timestamp": event.timestamp.isoformat(),
                "event_type": event.event_type.value,
                "user_id": event.user_id,
                "ip_address": event.ip_address,
                "platform": event.platform,
                "action": event.action,
                "resource": event.resource,
                "result": event.result,
                "details": _redact_details(event.details),
                "error_message": event.error_message,
            }
            
            # 根据结果选择日志级别
            if event.result == "success":
                self._logger.info(json.dumps(log_entry, ensure_ascii=False))
            elif event.result == "failure":
                self._logger.warning(json.dumps(log_entry, ensure_ascii=False))
            else:
                self._logger.error(json.dumps(log_entry, ensure_ascii=False))
        except Exception as e:
            logger.exception(f"Failed to log audit event: {e}")
    
    def log_login_start(self, ip_address: str, platform: str):
        """记录登录开始"""
        event = AuditEvent(
            timestamp=datetime.datetime.now(),
            event_type=AuditEventType.LOGIN_START,
            ip_address=ip_address,
            platform=platform,
            action=f"开始 {platform} 登录流程",
            result="success",
        )
        self.log_event(event)
    
    def log_login_success(self, ip_address: str, platform: str, details: dict[str, Any] = None):
        """记录登录成功"""
        event = AuditEvent(
            timestamp=datetime.datetime.now(),
            event_type=AuditEventType.LOGIN_SUCCESS,
            ip_address=ip_address,
            platform=platform,
            action=f"{platform} 登录成功",
            result="success",
            details=details or {},
        )
        self.log_event(event)
    
    def log_login_failure(self, ip_address: str, platform: str, error: str):
        """记录登录失败"""
        event = AuditEvent(
            timestamp=datetime.datetime.now(),
            event_type=AuditEventType.LOGIN_FAILURE,
            ip_address=ip_address,
            platform=platform,
            action=f"{platform} 登录失败",
            result="failure",
            error_message=error,
        )
        self.log_event(event)
    
    def log_logout(self, ip_address: str, platform: str):
        """记录登出"""
        event = AuditEvent(
            timestamp=datetime.datetime.now(),
            event_type=AuditEventType.LOGOUT,
            ip_address=ip_address,
            platform=platform,
            action=f"{platform} 登出",
            result="success",
        )
        self.log_event(event)
    
    def log_sync_favorites(self, ip_address: str, platform: str, count: int):
        """记录同步收藏夹"""
        event = AuditEvent(
            timestamp=datetime.datetime.now(),
            event_type=AuditEventType.SYNC_FAVORITES,
            ip_address=ip_address,
            platform=platform,
            action="同步收藏夹",
            result="success",
            details={"count": count},
        )
        self.log_event(event)
    
    def log_clear_knowledge(self, ip_address: str, platform: Optional[str] = None):
        """记录清空知识库"""
        event = AuditEvent(
            timestamp=datetime.datetime.now(),
            event_type=AuditEventType.CLEAR_KNOWLEDGE,
            ip_address=ip_address,
            platform=platform,
            action="清空知识库",
            result="success",
        )
        self.log_event(event)
    
    def log_settings_change(self, ip_address: str, setting_name: str, old_value: Any, new_value: Any):
        """记录设置变更"""
        sensitive = any(marker in setting_name.lower() for marker in _SECRET_FIELDS)
        event = AuditEvent(
            timestamp=datetime.datetime.now(),
            event_type=AuditEventType.SETTINGS_CHANGE,
            ip_address=ip_address,
            action=f"变更设置: {setting_name}",
            resource=setting_name,
            result="success",
            details={
                "old_value": "[REDACTED]" if sensitive else str(_redact_details(old_value)),
                "new_value": "[REDACTED]" if sensitive else str(_redact_details(new_value)),
            },
        )
        self.log_event(event)
    
    def log_unauthorized_access(self, ip_address: str, endpoint: str):
        """记录未授权访问"""
        event = AuditEvent(
            timestamp=datetime.datetime.now(),
            event_type=AuditEventType.UNAUTHORIZED_ACCESS,
            ip_address=ip_address,
            action="未授权访问尝试",
            resource=endpoint,
            result="failure",
        )
        self.log_event(event)


# 全局审计日志实例
_audit_logger: Optional[AuditLogger] = None


def get_audit_logger() -> AuditLogger:
    """获取全局审计日志实例"""
    global _audit_logger
    if _audit_logger is None:
        from app.core.config import settings
        log_dir = settings.project_root / "logs" / "audit"
        _audit_logger = AuditLogger(log_dir)
    return _audit_logger


def log_audit_event(event: AuditEvent):
    """快捷函数：记录审计事件"""
    get_audit_logger().log_event(event)
