"""Download video audio, publish complete MP3 caches, and protect active readers."""
from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from yt_dlp import YoutubeDL

from app.core.config import settings
from app.core.secure_storage import read_json

logger = logging.getLogger(__name__)
_cookie_lock = threading.Lock()
_download_lock = threading.Lock()  # 串行化 yt-dlp 网络探测/直连下载（反爬敏感段，2~3s）
_browser_semaphore = threading.BoundedSemaphore(
    max(1, min(4, settings.download_browser_concurrency))
)  # 浏览器兜底解析/下载并发上限（每路一个 headless Chromium）
_cache_lock = threading.RLock()
_active_items: dict[str, tuple[threading.RLock, int]] = {}
DOUYIN_REFERER = "https://www.douyin.com/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
PROTECTED_CACHE_FILENAMES = {"douyin_cookies.txt", ".gitkeep", "README.md"}


class MediaPipelineError(RuntimeError):
    """Download, validation, or transcode failure."""


@contextmanager
def audio_cache_lease(platform_item_id: str):
    """Protect download AND ASR readers; serialize repeated work on the same item."""
    # A lease is an internal cache key, not a Douyin-only numeric ID.  Keep a
    # strict filename-safe grammar so providers can namespace keys without
    # allowing traversal or unbounded lock-map growth.
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", platform_item_id):
        raise MediaPipelineError("无效的音频缓存键")
    with _cache_lock:
        lock, users = _active_items.get(platform_item_id, (threading.RLock(), 0))
        _active_items[platform_item_id] = (lock, users + 1)
    try:
        with lock:
            yield
    finally:
        with _cache_lock:
            _, users = _active_items[platform_item_id]
            if users == 1:
                del _active_items[platform_item_id]
            else:
                _active_items[platform_item_id] = (lock, users - 1)


def _find_project_ffmpeg_executable(repo_root: Path | None = None) -> Path | None:
    """Locate the project-local ffmpeg that scripts/bootstrap.ps1 may have installed.

    bootstrap.ps1 records the resolved ``bin\\`` directory in ``.runtime/ffmpeg-dir.txt``
    (same pattern as ``node-dir.txt``). Fall back to a recursive glob if that file is
    missing or stale: gyan.dev's essentials build extracts into an unpredictable
    versioned folder name (e.g. ``ffmpeg-8.0-essentials_build``), so the exact path
    can't be assumed -- mirrors ``douyin_collector._find_project_chromium_executable``.

    :param repo_root: override for tests; production callers always use the real repo root.
    """
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[3]
    state_file = repo_root / ".runtime" / "ffmpeg-dir.txt"
    if state_file.is_file():
        try:
            bin_dir = state_file.read_text(encoding="utf-8").strip()
        except OSError:
            bin_dir = ""
        if bin_dir:
            candidate = Path(bin_dir) / "ffmpeg.exe"
            if candidate.is_file():
                return candidate
    ffmpeg_root = repo_root / ".runtime" / "ffmpeg"
    if not ffmpeg_root.exists():
        return None
    for candidate in sorted(ffmpeg_root.glob("**/bin/ffmpeg.exe"), reverse=True):
        if candidate.is_file():
            return candidate
    return None


def _resolve_ffmpeg_path() -> str:
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    for candidate in (Path("C:/ffmpeg/bin/ffmpeg.exe"), Path.home() / "ffmpeg/bin/ffmpeg.exe"):
        if candidate.is_file():
            return str(candidate)
    project_local = _find_project_ffmpeg_executable()
    if project_local:
        return str(project_local)
    raise MediaPipelineError("未找到 ffmpeg，请安装 ffmpeg 并加入 PATH")


def _find_state_file() -> Path:
    configured = Path(settings.playwright_user_data_dir) / "state.json"
    if configured.is_file() or configured.with_name(configured.name + ".dpapi").is_file():
        return configured
    return Path(__file__).resolve().parent.parent / "storage/playwright_user_data/state.json"


def _get_audio_cache_dir() -> Path:
    # Relative like `api_settings_path`/`bilibili_state_path`: resolved
    # against the process CWD (`backend/` for every entry point), not
    # manually climbed from `__file__` — that style is what produced the
    # off-by-one `backend/app/app/...` path in bilibili/client.py (BUG-16).
    # See settings_store.py's `_store_path()` for the same convention.
    path = Path(settings.audio_cache_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_cookies(cookies: list, destination: Path) -> None:
    lines = ["# Netscape HTTP Cookie File", "# Generated from Playwright storage state"]
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        domain, name = str(cookie.get("domain") or ""), str(cookie.get("name") or "")
        if not domain or not name:
            continue
        try:
            expires = max(0, int(float(cookie.get("expires") or 0)))
        except (TypeError, ValueError, OverflowError):
            expires = 0
        fields = [domain, "TRUE" if domain.startswith(".") else "FALSE",
                  str(cookie.get("path") or "/"), "TRUE" if cookie.get("secure") else "FALSE",
                  str(expires), name, str(cookie.get("value") or "")]
        if any(any(char in field for char in "\r\n\t") for field in fields):
            continue
        lines.append("\t".join(fields))
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent,
                                     suffix=".tmp", delete=False) as output:
        temporary = Path(output.name)
        output.write("\n".join(lines) + "\n")
    try:
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def _export_cookiefile(destination: Path | None = None) -> Path:
    destination = destination or _get_audio_cache_dir() / "douyin_cookies.txt"
    with _cookie_lock:
        try:
            state = read_json(_find_state_file())
            if state is None:
                raise ValueError("missing state")
            cookies = state.get("cookies", [])
            if not isinstance(cookies, list):
                raise ValueError("invalid cookies")
            _write_cookies(cookies, destination)
        except (OSError, ValueError, AttributeError) as exc:
            raise MediaPipelineError("无法读取抖音登录状态，请重新扫码登录") from exc
    return destination


def _run_media_tool(command: list[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout,
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def _valid_mp3(path: Path, ffmpeg: str) -> bool:
    if not path.is_file() or path.stat().st_size < 128:
        return False
    probe = Path(ffmpeg).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
    try:
        result = _run_media_tool([
            str(probe), "-v", "error", "-select_streams", "a:0", "-show_entries",
            "stream=codec_name,sample_rate,channels:format=duration", "-of", "json", str(path)
        ], 15)
        data = json.loads(result.stdout)
        stream = data.get("streams", [{}])[0]
        return (result.returncode == 0 and stream.get("codec_name") == "mp3"
                and stream.get("sample_rate") == "16000" and stream.get("channels") == 1
                and float(data.get("format", {}).get("duration", 0)) > 0)
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError, TypeError):
        return False


def _transcode(source: Path, destination: Path, ffmpeg: str) -> None:
    try:
        result = _run_media_tool([
            ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
            "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", str(destination)
        ], 120)
    except subprocess.TimeoutExpired as exc:
        raise MediaPipelineError("ffmpeg 转码超时 (120 秒)，请重试") from exc
    if result.returncode != 0 or not _valid_mp3(destination, ffmpeg):
        raise MediaPipelineError(f"ffmpeg 音频转码失败: {result.stderr[-400:]}")


def transcode_audio_to_mp3(source: Path, destination: Path) -> None:
    """Convert provider audio to the single ASR-safe cache format."""
    ffmpeg = _resolve_ffmpeg_path()
    _transcode(source, destination, ffmpeg)


class _YDLLogger:
    def debug(self, message):
        pass

    def warning(self, message):
        pass

    def error(self, message):
        # YoutubeDL raises to the caller; don't log signed URLs or cookies.
        pass


def _download_raw(video_url: str, item_id: str, work_dir: Path, ffmpeg: str) -> Path:
    host = urlsplit(video_url).hostname or ""
    is_douyin = host == "douyin.com" or host.endswith(".douyin.com")
    options = {
        "outtmpl": str(work_dir / "raw.%(ext)s"), "format": "bestaudio/best",
        "noplaylist": True, "quiet": True, "noprogress": True, "no_warnings": True,
        "logger": _YDLLogger(), "retries": 2, "extractor_retries": 1,
        "fragment_retries": 2, "skip_unavailable_fragments": False,
        "cachedir": False, "socket_timeout": 20, "ffmpeg_location": ffmpeg,
        "http_headers": {"Referer": DOUYIN_REFERER, "User-Agent": USER_AGENT},
    }
    if is_douyin:
        try:
            options["cookiefile"] = str(_export_cookiefile(work_dir / "cookies.txt"))
        except MediaPipelineError:
            logger.warning("作品 %s 缺少可用登录状态", item_id)
    budget_start = [time.monotonic()]

    def check_download_budget(progress):
        if time.monotonic() - budget_start[0] > 240 or progress.get("downloaded_bytes", 0) > 256 * 1048576:
            raise MediaPipelineError("媒体下载超过时间或体积上限，请稍后重试")

    options["progress_hooks"] = [check_download_budget]
    options["max_filesize"] = 256 * 1048576

    # 阶段 1：yt-dlp 直连探测/下载 —— 串行（反爬敏感，单次 2~3s）
    primary_error = None
    with _download_lock:
        budget_start[0] = time.monotonic()
        try:
            with YoutubeDL(options) as ydl:
                ydl.extract_info(video_url, download=True)
        except Exception as exc:
            if not is_douyin:
                raise MediaPipelineError(f"音频下载失败 [{item_id}]") from exc
            primary_error = exc

    if primary_error is None:
        prefix = "raw."
    else:
        # 阶段 2：浏览器兜底解析 + 下载 —— 移出 _download_lock，受 semaphore 限并发
        logger.info("yt-dlp 详情接口不可用 [%s]，转用浏览器验证后的媒体地址（预期降级路径）", item_id)
        from app.services.douyin_media_resolver import DouyinMediaResolveError, resolve_douyin_media
        with _browser_semaphore:
            budget_start[0] = time.monotonic()
            try:
                media = resolve_douyin_media(item_id)
                options["outtmpl"] = str(work_dir / "browser.%(ext)s")
                options["http_headers"] = media["http_headers"]
                options.pop("cookiefile", None)
                if media.get("cookies"):
                    _write_cookies(media["cookies"], work_dir / "browser_cookies.txt")
                    options["cookiefile"] = str(work_dir / "browser_cookies.txt")
                with YoutubeDL(options) as ydl:
                    ydl.extract_info(media["url"], download=True)
            except Exception as fallback_error:
                reason = (str(fallback_error) if isinstance(fallback_error, DouyinMediaResolveError)
                          else f"媒体传输失败 ({type(fallback_error).__name__})")
                raise MediaPipelineError(
                    f"音频下载失败 [{item_id}]: yt-dlp 详情接口不可用，浏览器备用解析/下载失败；"
                    f"{reason}；请确认作品可播放、登录有效后重试"
                ) from None
        prefix = "browser."

    files = [p for p in work_dir.iterdir() if p.name.startswith(prefix)
             and p.suffix.lower() in {".mp4", ".m4a", ".mp3", ".webm", ".wav", ".aac", ".ogg"}
             and p.stat().st_size > 0]
    if len(files) != 1:
        raise MediaPipelineError(f"未找到完整且唯一的下载文件 [{item_id}]")
    return files[0]


def download_audio(video_url: str, platform_item_id: str) -> Path:
    with audio_cache_lease(platform_item_id):
        audio_dir = _get_audio_cache_dir()
        ffmpeg = _resolve_ffmpeg_path()
        target = audio_dir / f"{platform_item_id}.mp3"
        if _valid_mp3(target, ffmpeg):
            target.touch()
            logger.info("命中音频缓存 [%s]: %d bytes", platform_item_id, target.stat().st_size)
            return target
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix=f"download_{platform_item_id}_", dir=audio_dir) as work:
            work_dir = Path(work)
            legacy_wav = audio_dir / f"{platform_item_id}.wav"
            source = legacy_wav if legacy_wav.is_file() else _download_raw(
                video_url, platform_item_id, work_dir, ffmpeg)
            temporary_mp3 = work_dir / "audio.mp3"
            _transcode(source, temporary_mp3, ffmpeg)
            temporary_mp3.replace(target)
            if source == legacy_wav:
                legacy_wav.unlink(missing_ok=True)
        logger.info("音频就绪 [%s]: %d bytes, %.2f 秒", platform_item_id,
                    target.stat().st_size, time.monotonic() - started)
        return target


def _is_temporary(path: Path) -> bool:
    return ("_raw" in path.name or path.name.startswith("cookies_")
            or path.suffix.lower() in {".part", ".ytdl", ".tmp"})


def get_audio_cache_stats() -> dict:
    total = size = audio = temporary = 0
    for path in _get_audio_cache_dir().iterdir():
        if path.name in PROTECTED_CACHE_FILENAMES:
            continue
        try:
            if not path.is_file():
                continue
            size += path.stat().st_size
        except FileNotFoundError:
            continue
        total += 1
        if _is_temporary(path):
            temporary += 1
        else:
            audio += 1
    return {"total_files": total, "total_bytes": size, "total_mb": round(size / 1048576, 2),
            "audio_files": audio, "temp_files": temporary}


def clean_audio_cache(max_age_hours: float | None = None, max_size_mb: float | None = None) -> dict:
    age_limit = settings.audio_cache_retention_hours if max_age_hours is None else max_age_hours
    size_limit = settings.audio_cache_max_size_mb if max_size_mb is None else max_size_mb
    if not all(math.isfinite(v) and v >= 0 for v in (age_limit, size_limit)):
        raise ValueError("音频缓存时长和容量必须是非负有限数值")
    deleted = freed = 0
    now = time.time()
    with _cache_lock:
        candidates = []
        protected_bytes = 0
        for path in _get_audio_cache_dir().iterdir():
            if path.name in PROTECTED_CACHE_FILENAMES:
                continue
            if path.is_dir():
                match = re.fullmatch(r"download_([0-9]{1,32})_[a-zA-Z0-9_-]+", path.name)
                # Recover only our abandoned work dirs, never a linked or active
                # directory. Resolve containment before any recursive deletion.
                if (not match or match[1] in _active_items or path.is_symlink()
                        or path.resolve().parent != _get_audio_cache_dir().resolve()):
                    continue
                try:
                    files = [entry for entry in path.iterdir() if entry.is_file()]
                    newest = max([path.stat().st_mtime] + [entry.stat().st_mtime for entry in files])
                    if now - newest > 1800:
                        size = sum(entry.stat().st_size for entry in files)
                        shutil.rmtree(path)
                        deleted += len(files)
                        freed += size
                except OSError:
                    logger.debug("暂不能回收中断下载目录: %s", path.name)
                continue
            if not path.is_file():
                continue
            try:
                stat = path.stat()
                if any(path.name == f"{item}.mp3" or path.name == f"{item}.wav"
                       or path.name.startswith(f"{item}_raw.") for item in _active_items):
                    protected_bytes += stat.st_size
                    continue
                age = now - stat.st_mtime
                if age > age_limit * 3600 or (_is_temporary(path) and age > 1800):
                    path.unlink()
                    deleted += 1
                    freed += stat.st_size
                else:
                    candidates.append((stat.st_mtime, path, stat.st_size))
            except OSError:
                logger.debug("缓存文件暂不可清理: %s", path.name)
        remaining = protected_bytes + sum(row[2] for row in candidates)
        if remaining > size_limit * 1048576:
            for _, path, size in sorted(candidates):
                if remaining <= size_limit * 1048576 * .8:
                    break
                try:
                    path.unlink()
                except OSError:
                    continue
                deleted += 1
                freed += size
                remaining -= size
    return {"deleted_files": deleted, "freed_bytes": freed, "freed_mb": round(freed / 1048576, 2)}


_cleaner_thread: threading.Thread | None = None
_cleaner_stop = threading.Event()
_cleaner_lock = threading.Lock()


def start_cache_cleaner_scheduler() -> None:
    global _cleaner_thread
    with _cleaner_lock:
        if _cleaner_thread and _cleaner_thread.is_alive():
            return
        _cleaner_stop.clear()

        def cleanup_loop():
            while not _cleaner_stop.is_set():
                try:
                    clean_audio_cache()
                except Exception:
                    logger.exception("音频缓存定时清理失败")
                _cleaner_stop.wait(max(1, settings.audio_cache_clean_interval_seconds))

        _cleaner_thread = threading.Thread(target=cleanup_loop, daemon=True, name="audio-cache-cleaner")
        _cleaner_thread.start()


def stop_cache_cleaner_scheduler() -> None:
    with _cleaner_lock:
        _cleaner_stop.set()
        if _cleaner_thread:
            _cleaner_thread.join(timeout=2)
