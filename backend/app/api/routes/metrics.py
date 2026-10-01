"""
系统监控指标 API

仅在开发者模式下启用，提供系统资源使用、网络状态、缓存统计等诊断信息。
"""
import os
import platform
import time
from pathlib import Path
from stat import S_ISREG
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import psutil
from fastapi import APIRouter, Depends, HTTPException

from app.core.config import settings
from app.core.network import detect_network_proxy
from app.core.security import require_local_client
from app.db.session import get_db
from sqlalchemy.orm import Session

router = APIRouter(prefix="/metrics", tags=["metrics"], dependencies=[Depends(require_local_client)])
# Desktop diagnostics are local resource reads, not telemetry. Some browser
# filters replace /metrics/* with a 499 transparent image before it reaches us.
# Keep the documented API and expose an internal alias for the bundled panel.
diagnostics_router = APIRouter(
    prefix="/system/diagnostics", include_in_schema=False,
    dependencies=[Depends(require_local_client)],
)

# 启动时间（用于计算运行时长）
_START_TIME = time.time()


@router.get("/system")
@diagnostics_router.get("/system")
def get_system_metrics() -> dict[str, Any]:
    """
    获取系统资源指标
    
    返回：CPU、内存、磁盘使用情况
    """
    if not settings.developer_mode:
        raise HTTPException(status_code=403, detail="开发者模式未启用")
    
    process = psutil.Process(os.getpid())
    
    # A fresh Process object needs a sampling interval; its first nonblocking
    # read is always zero. This synchronous route runs in FastAPI's thread pool.
    cpu_percent = process.cpu_percent(interval=0.1)
    system_cpu_percent = psutil.cpu_percent(interval=None)
    
    # 内存使用（进程级 + 系统级）
    memory_info = process.memory_info()
    system_memory = psutil.virtual_memory()
    
    # 磁盘使用
    project_root = settings.project_root
    disk_usage = psutil.disk_usage(str(project_root))
    
    # 运行时长
    uptime_seconds = time.time() - _START_TIME
    
    return {
        "process": {
            "pid": process.pid,
            "cpu_percent": round(cpu_percent, 2),
            "memory_mb": round(memory_info.rss / 1024 / 1024, 2),
            "memory_percent": round(process.memory_percent(), 2),
            "num_threads": process.num_threads(),
            "uptime_seconds": round(uptime_seconds, 2),
        },
        "system": {
            "platform": platform.platform(),
            "python_version": platform.python_version(),
            "cpu_count": psutil.cpu_count(),
            "cpu_percent": round(system_cpu_percent, 2),
            "memory_total_mb": round(system_memory.total / 1024 / 1024, 2),
            "memory_available_mb": round(system_memory.available / 1024 / 1024, 2),
            "memory_percent": round(system_memory.percent, 2),
            "disk_total_gb": round(disk_usage.total / 1024 / 1024 / 1024, 2),
            "disk_used_gb": round(disk_usage.used / 1024 / 1024 / 1024, 2),
            "disk_percent": round(disk_usage.percent, 2),
        },
    }


@router.get("/network")
@diagnostics_router.get("/network")
def get_network_metrics() -> dict[str, Any]:
    """
    获取网络状态指标
    
    返回：代理配置、网络接口统计
    """
    if not settings.developer_mode:
        raise HTTPException(status_code=403, detail="开发者模式未启用")
    
    # 代理检测
    proxy = detect_network_proxy(validate=False)
    proxy_display = None
    if proxy:
        # A diagnostic response must never expose the proxy's user/password.
        parts = urlsplit(proxy)
        host = parts.hostname or ""
        host = f"[{host}]" if ":" in host else host
        netloc = f"{host}:{parts.port}" if parts.port is not None else host
        proxy_display = urlunsplit((parts.scheme, netloc, "", "", ""))
    proxy_info = {
        "detected": proxy is not None,
        "url": proxy_display,
        "mode": "proxy" if proxy else "direct/tun",
    }
    
    # 网络接口统计
    net_io = psutil.net_io_counters()
    
    return {
        "proxy": proxy_info,
        "io_counters": {
            "bytes_sent_mb": round(net_io.bytes_sent / 1024 / 1024, 2),
            "bytes_recv_mb": round(net_io.bytes_recv / 1024 / 1024, 2),
            "packets_sent": net_io.packets_sent,
            "packets_recv": net_io.packets_recv,
            "errin": net_io.errin,
            "errout": net_io.errout,
            "dropin": net_io.dropin,
            "dropout": net_io.dropout,
        },
    }


@router.get("/cache")
@diagnostics_router.get("/cache")
def get_cache_metrics() -> dict[str, Any]:
    """
    获取缓存统计
    
    返回：音频缓存、向量库、数据库大小
    """
    if not settings.developer_mode:
        raise HTTPException(status_code=403, detail="开发者模式未启用")
    
    def file_size(path: Path | None) -> int:
        # Cleanup/checkpoint workers can remove a cache or WAL file between
        # discovery and stat. Monitoring must tolerate that normal race.
        if path is None:
            return 0
        try:
            info = path.stat()
            return info.st_size if S_ISREG(info.st_mode) else 0
        except OSError:
            return 0

    def directory_stats(path: Path) -> tuple[int, int]:
        total, count = 0, 0
        try:
            for item in path.rglob("*"):
                try:
                    info = item.stat()
                    if S_ISREG(info.st_mode):
                        total += info.st_size
                        count += 1
                except OSError:
                    continue
        except OSError:
            pass
        return total, count
    
    # 音频缓存
    audio_cache_path = Path(settings.audio_cache_dir)
    audio_cache_size, audio_file_count = directory_stats(audio_cache_path)
    
    # 向量库
    chroma_path = Path(settings.chroma_persist_dir)
    chroma_size, _ = directory_stats(chroma_path)
    
    # 数据库
    from app.db.session import engine
    db_path = Path(engine.url.database) if engine.dialect.name == "sqlite" and engine.url.database else None
    db_size = file_size(db_path)
    
    # WAL 文件
    wal_path = Path(str(db_path) + "-wal") if db_path else None
    wal_size = file_size(wal_path)
    
    return {
        "audio_cache": {
            "size_mb": round(audio_cache_size / 1024 / 1024, 2),
            "file_count": audio_file_count,
            "path": str(audio_cache_path),
            "max_size_mb": settings.audio_cache_max_size_mb,
            "retention_hours": settings.audio_cache_retention_hours,
        },
        "vector_db": {
            "size_mb": round(chroma_size / 1024 / 1024, 2),
            "path": str(chroma_path),
        },
        "sqlite": {
            "db_size_mb": round(db_size / 1024 / 1024, 2),
            "wal_size_mb": round(wal_size / 1024 / 1024, 2),
            "total_size_mb": round((db_size + wal_size) / 1024 / 1024, 2),
        },
    }


@router.get("/database")
@diagnostics_router.get("/database")
def get_database_metrics(session: Session = Depends(get_db)) -> dict[str, Any]:
    """
    获取数据库统计
    
    返回：表记录数、连接池状态
    """
    if not settings.developer_mode:
        raise HTTPException(status_code=403, detail="开发者模式未启用")
    
    from app.models.entities import (
        SourceAccount,
        FavoriteCollection,
        ContentItem,
        CollectionItemRelation,
        IngestionItem,
    )
    
    return {
        "tables": {
            "source_accounts": session.query(SourceAccount).count(),
            "favorite_collections": session.query(FavoriteCollection).count(),
            "content_items": session.query(ContentItem).count(),
            "collection_items": session.query(CollectionItemRelation).count(),
            "ingestion_items": session.query(IngestionItem).count(),
        },
    }


@router.get("/health")
def get_health_check() -> dict[str, Any]:
    """
    健康检查端点（不需要开发者模式）
    
    返回：服务状态、运行时长
    """
    uptime_seconds = time.time() - _START_TIME
    
    return {
        "status": "healthy",
        "uptime_seconds": round(uptime_seconds, 2),
        "developer_mode": settings.developer_mode,
        "timestamp": time.time(),
    }


@router.get("/audit/recent")
def get_recent_audit_logs(limit: int = 50) -> dict[str, Any]:
    """
    获取最近的审计日志
    
    Args:
        limit: 返回的日志条数（默认 50，最大 500）
    
    返回：审计日志列表
    """
    if not settings.developer_mode:
        raise HTTPException(status_code=403, detail="开发者模式未启用")
    
    from app.core.config import settings as app_settings
    
    limit = min(max(1, limit), 500)
    audit_log_file = app_settings.project_root / "logs" / "audit" / "audit.log"
    
    if not audit_log_file.exists():
        return {"logs": [], "count": 0}
    
    try:
        logs = []
        from app.api.routes.system import _tail_lines
        for line in reversed(_tail_lines(audit_log_file, max_lines=limit)):
            try:
                import json
                log_entry = json.loads(line)
                logs.append(log_entry)
            except json.JSONDecodeError:
                continue
        
        return {
            "logs": logs,
            "count": len(logs),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取审计日志失败: {e}")


@router.get("/performance")
def get_performance_metrics() -> dict[str, Any]:
    """
    获取性能指标
    
    返回：请求延迟、队列长度、错误率等
    """
    if not settings.developer_mode:
        raise HTTPException(status_code=403, detail="开发者模式未启用")
    
    process = psutil.Process(os.getpid())
    
    # 文件描述符/句柄使用情况
    try:
        if hasattr(process, 'num_fds'):
            open_files = process.num_fds()
        else:
            open_files = len(process.open_files())
    except Exception:
        open_files = -1
    
    # 线程详情
    try:
        threads = [{"id": t.id, "user_time": t.user_time, "system_time": t.system_time} 
                   for t in process.threads()]
    except Exception:
        threads = []
    
    return {
        "open_files": open_files,
        "threads": threads,
        "thread_count": len(threads),
        "connections": len(process.connections()) if hasattr(process, 'connections') else -1,
    }
