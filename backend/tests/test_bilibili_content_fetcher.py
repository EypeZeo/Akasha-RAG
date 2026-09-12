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
        get_audio_url=lambda *_: _async("https://upos-sz-mirrorcos.bilivideo.com/audio.m4a"),
        download_audio_to_file=lambda _url, path: _download(path),
    )

    monkeypatch.setattr(
        "app.services.bilibili.content_fetcher.transcode_audio_to_mp3",
        lambda source, destination: destination.write_bytes(b"mp3 fixture"),
    )
    monkeypatch.setattr(
        "app.services.bilibili.content_fetcher.asr_service.transcribe_to_text",
        lambda path: "A sufficiently long fallback transcript proves that raw M4A is transcoded before ASR.",
    )

    text = await fetcher.fetch_transcript("BV1xx411c7mD", 1001, title="测试视频")
    assert "视频语音转写" in text
    assert not list(tmp_path.iterdir())


async def _async(value):
    return value


async def _download(path):
    path.write_bytes(b"m4a fixture")
    return True

