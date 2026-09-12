"""本地音频转写入口，使用可终止的工作进程隔离 DashScope WebSocket。"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time
from collections.abc import Callable

from app.core.config import settings
from app.services.text_processing import RawSegment

logger = logging.getLogger(__name__)
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_MODEL_ALIASES = {
    "paraformer-v2": "paraformer-realtime-v2",
    "paraformer-v1": "paraformer-realtime-v1",
}


class ASRService:
    """同步转写；超时会终止并回收进程，不留下后台识别任务。"""

    def transcribe(
        self,
        audio_path: Path,
        cancel_check: Callable[[], bool] | None = None,
    ) -> tuple[list[RawSegment], str]:
        audio_path = audio_path.resolve(strict=True)
        if not audio_path.is_file():
            raise ValueError("ASR 输入必须是音频文件")
        size = audio_path.stat().st_size
        if size == 0:
            raise ValueError("ASR 音频文件为空，请重新下载")
        if size > settings.asr_max_audio_size_mb * 1024 * 1024:
            raise ValueError(
                f"ASR 音频超过 {settings.asr_max_audio_size_mb:g} MB 上限，"
                "请先转为 16kHz 单声道轻量 MP3"
            )
        fmt = audio_path.suffix.lower().lstrip(".")
        if fmt not in {"mp3", "wav"}:
            raise ValueError(f"ASR 不支持 {audio_path.suffix} 容器，请先转为 MP3 或 PCM WAV")
        from app.core.config import require_dashscope_key
        api_key = require_dashscope_key()
        configured_model = settings.asr_model.strip()
        model = _MODEL_ALIASES.get(configured_model, configured_model)
        if model not in {"paraformer-realtime-v1", "paraformer-realtime-v2"}:
            raise ValueError(f"ASR 模型 {configured_model!r} 不适用于当前 16kHz 实时识别接口")

        logger.info("开始语音转写: %s (%d bytes), model=%s", audio_path.name, size, model)
        request = {
            "audio_path": str(audio_path),
            "format": fmt,
            "model": model,
            "api_key": api_key,
            "request_timeout": settings.asr_timeout_seconds,
        }
        # SDK 的 call() 预载整文件；size 上限约束单任务内存。
        # SDK 的 stop() 会无期限 join()，线程池 timeout 无法取消该等待。
        # run() 的 timeout 则会 kill + wait，连同子进程中的 WebSocket 一起回收。
        child_env = os.environ.copy()
        child_env["PYTHONIOENCODING"] = "utf-8"
        child_env.pop("DASHSCOPE_API_KEY", None)
        child_env.pop("DEEPSEEK_API_KEY", None)
        executable = sys.executable
        if os.name == "nt" and getattr(sys, "_base_executable", None):
            # Windows venv/python.exe redirects to a second process. Launch the
            # interpreter directly while retaining the venv's path configuration,
            # so timeout kills the actual SDK process, not just its redirector.
            executable = sys._base_executable
            child_env["__PYVENV_LAUNCHER__"] = sys.executable
        command = [executable, "-m", "app.services.asr_worker"]
        run_kwargs = dict(
            input=json.dumps(request, ensure_ascii=False),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=_BACKEND_ROOT,
            env=child_env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            if cancel_check is None:
                completed = subprocess.run(
                    command,
                    timeout=settings.asr_timeout_seconds,
                    check=False,
                    **run_kwargs,
                )
            else:
                process = subprocess.Popen(command, stdin=subprocess.PIPE, **{
                    key: value for key, value in run_kwargs.items() if key != "input"
                })
                deadline = time.monotonic() + settings.asr_timeout_seconds
                payload = run_kwargs["input"]
                sent_input = False
                while True:
                    try:
                        completed_stdout, _ = process.communicate(
                            input=None if sent_input else payload,
                            timeout=min(0.25, max(0.01, deadline - time.monotonic())),
                        )
                        completed = subprocess.CompletedProcess(command, process.returncode, completed_stdout)
                        break
                    except subprocess.TimeoutExpired:
                        sent_input = True
                        if cancel_check():
                            process.kill()
                            process.communicate()
                            raise RuntimeError("ASR 转写已取消，工作进程已终止")
                        if time.monotonic() >= deadline:
                            process.kill()
                            process.communicate()
                            raise subprocess.TimeoutExpired(command, settings.asr_timeout_seconds)
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"ASR 转写超时 [{audio_path.name}]: "
                f"超过 {settings.asr_timeout_seconds:g} 秒，工作进程已终止；可稍后重试"
            ) from None
        except OSError as exc:
            raise RuntimeError(f"无法启动 ASR 工作进程 [{audio_path.name}]") from exc

        if completed.returncode != 0:
            raise RuntimeError(
                f"ASR 工作进程异常退出 [{audio_path.name}]: exit={completed.returncode}"
            )
        try:
            response = json.loads(completed.stdout)
            if not isinstance(response, dict):
                raise ValueError("response must be an object")
            if response.get("error"):
                error = str(response["error"]).replace(api_key, "[redacted]")[:1000]
                raise RuntimeError(f"ASR 转写失败 [{audio_path.name}]: {error}")
            sentences = response["segments"]
            if not isinstance(sentences, list):
                raise ValueError("segments must be a list")
            segments = [RawSegment(**sentence) for sentence in sentences]
        except (ValueError, TypeError, KeyError) as exc:
            raise RuntimeError(f"ASR 工作进程返回格式无效 [{audio_path.name}]") from exc

        logger.info("ASR 转写完成: %s, %d 段落", audio_path.name, len(segments))
        return segments, "zh"

    def transcribe_to_text(
        self,
        audio_path: Path,
        cancel_check: Callable[[], bool] | None = None,
    ) -> str:
        """将音频转写为纯文本。"""
        segments, _ = self.transcribe(audio_path, cancel_check=cancel_check)
        return "\n".join(seg.text for seg in segments)


asr_service = ASRService()
