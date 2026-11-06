"""Regression tests for Bilibili audio ASR fallback."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.bilibili.content_fetcher import BilibiliContentFetcher


@pytest.mark.asyncio
async def test_no_subtitle_fallback_transcodes_before_asr(tmp_path, monkeypatch):
    fetcher = BilibiliContentFetcher()
    fetcher._cache_dir = tmp_path
    fetcher._client = SimpleNamespace(
        get_player_info=lambda *_: _async({}),
        get_audio_urls=lambda *_: _async(["https://upos-sz-mirrorcos.bilivideo.com/audio.m4a"]),
        download_audio_to_file=lambda _url, path: _download(path),
    )

    monkeypatch.setattr(
        "app.services.bilibili.content_fetcher.transcode_audio_to_mp3",
        lambda source, destination: destination.write_bytes(b"mp3 fixture"),
    )
    monkeypatch.setattr(
        "app.services.bilibili.content_fetcher.asr_service.transcribe_to_text",
        lambda path, cancel_check=None: "A sufficiently long fallback transcript proves that raw M4A is transcoded before ASR.",
    )

    text = await fetcher.fetch_transcript("BV1xx411c7mD", 1001, title="测试视频")
    assert "视频语音转写" in text
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_audio_download_tries_ordered_cdn_candidates_for_same_cid(tmp_path, monkeypatch):
    fetcher = BilibiliContentFetcher()
    fetcher._cache_dir = tmp_path
    candidates = [
        "https://primary.bilivideo.com/audio.m4a",
        "https://backup.bilivideo.com/audio.m4a",
    ]
    requested = []
    downloaded = []

    async def get_audio_urls(bvid, cid):
        requested.append((bvid, cid))
        return candidates

    async def download_audio_to_file(url, path):
        downloaded.append(url)
        if url == candidates[1]:
            path.write_bytes(b"m4a fixture")
            return True
        return False

    fetcher._client = SimpleNamespace(
        get_player_info=lambda *_: _async({}),
        get_audio_urls=get_audio_urls,
        download_audio_to_file=download_audio_to_file,
    )
    monkeypatch.setattr(
        "app.services.bilibili.content_fetcher.transcode_audio_to_mp3",
        lambda source, destination: destination.write_bytes(b"mp3 fixture"),
    )
    monkeypatch.setattr(
        "app.services.bilibili.content_fetcher.asr_service.transcribe_to_text",
        lambda path, cancel_check=None: "A sufficiently long fallback transcript succeeds after the primary CDN fails.",
    )

    text = await fetcher.fetch_transcript("BV1xx411c7mD", 1001, title="测试视频")

    assert "视频语音转写" in text
    assert requested == [("BV1xx411c7mD", 1001)]
    assert downloaded == candidates
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_audio_download_fails_after_all_cdn_candidates_fail(tmp_path, monkeypatch):
    fetcher = BilibiliContentFetcher()
    fetcher._cache_dir = tmp_path
    candidates = [
        "https://primary.bilivideo.com/audio.m4a",
        "https://backup.bilivideo.com/audio.m4a",
    ]
    attempted = []

    async def download_audio_to_file(url, _path):
        attempted.append(url)
        return False

    fetcher._client = SimpleNamespace(
        get_player_info=lambda *_: _async({}),
        get_audio_urls=lambda *_: _async(candidates),
        download_audio_to_file=download_audio_to_file,
    )
    def asr(*_args, **_kwargs):
        pytest.fail("ASR must not run when every CDN candidate fails")

    monkeypatch.setattr("app.services.bilibili.content_fetcher.asr_service.transcribe_to_text", asr)

    with pytest.raises(RuntimeError, match="音频流下载失败或文件损坏"):
        await fetcher.fetch_transcript("BV1xx411c7mD", 1001, title="测试视频")

    assert attempted == candidates
    assert not list(tmp_path.iterdir())


async def _async(value):
    return value


async def _download(path):
    path.write_bytes(b"m4a fixture")
    return True

