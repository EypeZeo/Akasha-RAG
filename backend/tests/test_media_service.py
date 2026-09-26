"""Regression tests for cache publication, concurrent readers and cookie isolation."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app.services import media_service as media


@pytest.fixture
def cache(monkeypatch, tmp_path):
    monkeypatch.setattr(media.settings, "audio_cache_dir", str(tmp_path))
    return tmp_path


def test_cleaner_protects_asr_reader_until_lease_is_released(cache):
    active = cache / "123.mp3"
    idle = cache / "456.mp3"
    active.write_bytes(b"a" * 100)
    idle.write_bytes(b"b" * 100)
    with media.audio_cache_lease("123"):
        result = media.clean_audio_cache(max_age_hours=0, max_size_mb=0)
        assert result["deleted_files"] == 1
        assert active.exists() and not idle.exists()
        with media.audio_cache_lease("123"):
            assert media._active_items["123"][1] == 2
    assert "123" not in media._active_items
    # The lease is gone, so the file may be reclaimed by age. It was written a moment ago, though, and on Windows
    # time.time() only ticks every ~15.6 ms: "older than 0 hours" would hold only if a tick happened to pass.
    os.utime(active, (1, 1))
    assert media.clean_audio_cache(max_age_hours=0)["deleted_files"] == 1


def test_cleaner_removes_only_its_abandoned_work_directories(cache):
    abandoned = cache / "download_123_old"
    active = cache / "download_456_active"
    unrelated = cache / "user_directory"
    for directory in (abandoned, active, unrelated):
        directory.mkdir()
        item = directory / "raw.mp4"
        item.write_bytes(b"fragment")
        os.utime(item, (1, 1))
        os.utime(directory, (1, 1))
    with media.audio_cache_lease("456"):
        assert media.clean_audio_cache()["deleted_files"] == 1
    assert not abandoned.exists()
    assert active.exists() and unrelated.exists()


def test_same_item_calls_publish_once_and_return_same_cache(cache, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    calls = []
    monkeypatch.setattr(media, "_resolve_ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr(media, "_valid_mp3", lambda path, _: path.exists())

    def download(url, item, directory, ffmpeg):
        calls.append(item)
        entered.set()
        assert release.wait(5)
        source = directory / "raw.mp4"
        source.write_bytes(b"source")
        return source

    def transcode(source, target, ffmpeg):
        assert not (cache / "123.mp3").exists()
        target.write_bytes(b"completed audio")

    monkeypatch.setattr(media, "_download_raw", download)
    monkeypatch.setattr(media, "_transcode", transcode)
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(media.download_audio, "https://www.douyin.com/video/123", "123")
        assert entered.wait(5)
        two = pool.submit(media.download_audio, "https://www.douyin.com/video/123", "123")
        release.set()
        assert one.result(timeout=5) == two.result(timeout=5) == cache / "123.mp3"
    assert calls == ["123"]
    assert list(cache.iterdir()) == [cache / "123.mp3"]


def test_transcode_failure_never_publishes_partial_mp3_or_returns_raw(cache, monkeypatch):
    monkeypatch.setattr(media, "_resolve_ffmpeg_path", lambda: "ffmpeg")
    legacy = cache / "123.wav"
    legacy.write_bytes(b"source")

    def failed(command, timeout):
        Path(command[-1]).write_bytes(b"partial output")
        return SimpleNamespace(returncode=1, stderr="invalid audio")

    monkeypatch.setattr(media, "_run_media_tool", failed)
    with pytest.raises(media.MediaPipelineError, match="转码失败"):
        media.download_audio("https://www.douyin.com/video/123", "123")
    assert legacy.exists()
    assert not (cache / "123.mp3").exists()
    assert not list(cache.glob("download_*"))


def test_cookie_export_does_not_reuse_stale_master(cache, monkeypatch):
    state = cache / "state.json"
    state.write_text(json.dumps({"cookies": [{"domain": ".douyin.com", "name": "sessionid",
                                             "value": "fresh", "path": "/", "expires": -1}]}))
    monkeypatch.setattr(media, "_find_state_file", lambda: state)
    master = cache / "douyin_cookies.txt"
    master.write_text("stale cache" * 20)
    exported = media._export_cookiefile(cache / "isolated.txt")
    assert "fresh" in exported.read_text()
    assert master.read_text() == "stale cache" * 20


def test_fresh_cookie_error_uses_verified_media_and_isolated_cookiefile(cache, monkeypatch):
    from app.services import douyin_media_resolver as resolver
    state = cache / "state.json"
    state.write_text('{"cookies": []}')
    monkeypatch.setattr(media, "_find_state_file", lambda: state)
    monkeypatch.setattr(resolver, "resolve_douyin_media", lambda _: {
        "url": "https://v.douyinvod.com/media.mp4", "http_headers": {"User-Agent": "browser"},
        "cookies": [],
    })
    options_seen = []

    class YDL:
        def __init__(self, options):
            self.options = dict(options)
            options_seen.append(self.options)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download):
            if "www.douyin.com" in url:
                (cache / "raw.mp4.part").write_bytes(b"partial")
                raise RuntimeError("Fresh cookies needed")
            Path(self.options["outtmpl"].replace("%(ext)s", "mp4")).write_bytes(b"complete")

    monkeypatch.setattr(media, "YoutubeDL", YDL)
    raw = media._download_raw("https://www.douyin.com/video/123", "123", cache, "ffmpeg")
    assert raw.name == "browser.mp4"
    assert Path(options_seen[0]["cookiefile"]).name == "cookies.txt"
    assert "cookiefile" not in options_seen[1]
    assert "Cookie" not in options_seen[1]["http_headers"]


@pytest.mark.parametrize("item", ["../123", "123/456", "123*", ""])
def test_invalid_ids_cannot_escape_cache_directory(cache, item):
    with pytest.raises(media.MediaPipelineError):
        media.download_audio("https://www.douyin.com/video/123", item)
    assert not list(cache.iterdir())


def test_real_ffmpeg_converts_legacy_wav_and_validates_mp3(cache):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg integration requires local executable")
    wav = cache / "123.wav"
    subprocess.run([ffmpeg, "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=0.3", str(wav)], check=True, timeout=15,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    result = media.download_audio("https://www.douyin.com/video/123", "123")
    assert media._valid_mp3(result, ffmpeg)
    assert not wav.exists()
    assert not list(cache.glob("download_*"))
