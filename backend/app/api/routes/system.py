"""
系统管理路由模块

提供系统运行状态、动态日志级别调整、本地目录一键打开等管理功能。
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.logging import LOG_DIR, VALID_LEVELS, log_manager

router = APIRouter(prefix="/system", tags=["系统管理"])

# 目录选择框互斥：同一时刻只允许一个原生对话框在等待
_pick_dir_lock = threading.Lock()

_PICK_DIR_SCRIPT = r"""
import sys, tkinter as tk
from tkinter import filedialog
root = tk.Tk()
root.withdraw()
try:
    root.attributes('-topmost', True)
    root.lift()
    root.focus_force()
except Exception:
    pass
init_dir = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1].strip() else None
path = filedialog.askdirectory(initialdir=init_dir, title='选择导出保存文件夹')
try:
    root.destroy()
except Exception:
    pass
sys.stdout.write(path or '')
"""


class SetLogLevelRequest(BaseModel):
    level: str
    persist: bool = True


class OpenFolderRequest(BaseModel):
    folder_type: str = "logs"  # "logs" | "export" | "custom"
    custom_path: Optional[str] = None


@router.get("/log-level")
async def get_log_level():
    """获取当前日志级别及可用选项"""
    return {
        "success": True,
        "current_level": log_manager.get_level(),
        "available_levels": VALID_LEVELS,
    }


@router.post("/log-level")
async def set_log_level(body: SetLogLevelRequest):
    """
    动态更新控制台日志级别（实时应用生效并持久化）
    """
    ok = log_manager.set_level(body.level, persist=body.persist)
    if not ok:
        return {
            "success": False,
            "message": f"无效的日志级别: {body.level}，可选: {VALID_LEVELS}",
        }
    return {
        "success": True,
        "current_level": log_manager.get_level(),
        "message": f"日志级别已切换为 {log_manager.get_level()}",
    }


@router.post("/open-folder")
async def open_local_folder(body: OpenFolderRequest):
    """
    在 Windows 资源管理器中一键打开本地文件夹
    """
    target_path: Optional[Path] = None

    if body.folder_type == "logs":
        target_path = LOG_DIR
    elif body.folder_type == "custom" and body.custom_path:
        target_path = Path(body.custom_path)
    else:
        # 默认打开 logs
        target_path = LOG_DIR

    if not target_path or not target_path.exists():
        # 如果不存在则尝试创建
        try:
            target_path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return {"success": False, "message": f"目录不存在且创建失败: {e}"}

    abs_path = str(target_path.resolve())

    try:
        if sys.platform == "win32":
            os.startfile(abs_path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", abs_path])
        else:
            subprocess.Popen(["xdg-open", abs_path])

        return {"success": True, "path": abs_path}
    except Exception as exc:
        return {"success": False, "message": f"打开目录失败: {exc}"}


class PickDirectoryRequest(BaseModel):
    initial_dir: Optional[str] = None


@router.post("/pick-directory")
async def pick_directory(body: PickDirectoryRequest = PickDirectoryRequest()):
    """
    弹出系统原生「选择文件夹」对话框，返回用户选中的绝对路径。

    仅适用于后端与浏览器在同一台机器的本地部署场景。
    """
    if not _pick_dir_lock.acquire(blocking=False):
        return {"success": False, "busy": True, "message": "已有选择框在等待，请先完成"}
    try:
        init_dir = (body.initial_dir or "").strip()
        completed = subprocess.run(
            [sys.executable, "-c", _PICK_DIR_SCRIPT, init_dir],
            capture_output=True,
            text=True,
            timeout=180,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        picked = (completed.stdout or "").strip()
        if not picked:
            return {"success": False, "cancelled": True}
        return {"success": True, "path": str(Path(picked).resolve())}
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "选择超时"}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "message": f"无法打开目录选择框: {exc}"}
    finally:
        _pick_dir_lock.release()


@router.get("/logs")
async def get_recent_logs(lines: int = 100, log_type: str = "all"):
    """
    读取最新的本地日志片段，用于前端控制台或排错调试

    :param lines: 返回的最大行数
    :param log_type: "all" | "error" | "terminal"
    """
    try:
        pattern = f"{log_type}_*.log"
        matching_files = sorted(LOG_DIR.glob(pattern), reverse=True)
        if not matching_files:
            return {"success": True, "lines": [], "file": None}

        latest_file = matching_files[0]
        content_lines: List[str] = []
        with open(latest_file, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
            content_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines

        return {
            "success": True,
            "file": latest_file.name,
            "lines": [l.rstrip("\r\n") for l in content_lines],
            "total_lines": len(content_lines),
        }
    except Exception as e:
        return {"success": False, "message": f"读取日志失败: {e}"}


@router.get("/audio-cache-stats")
async def get_audio_cache_info():
    """
    获取音频缓存目录 (audio_cache) 的当前占用大小与文件数量统计
    """
    from app.services.media_service import get_audio_cache_stats
    stats = get_audio_cache_stats()
    return {"success": True, "stats": stats}


class CleanAudioCacheRequest(BaseModel):
    max_age_hours: Optional[float] = None
    max_size_mb: Optional[float] = None


@router.post("/clean-audio-cache")
async def clean_audio_cache_endpoint(body: CleanAudioCacheRequest = CleanAudioCacheRequest()):
    """
    手动触发音频缓存目录清理（支持自定义保留时长和最大容量）
    """
    from app.services.media_service import clean_audio_cache
    res = clean_audio_cache(
        max_age_hours=body.max_age_hours,
        max_size_mb=body.max_size_mb,
    )
    return {"success": True, "result": res}

