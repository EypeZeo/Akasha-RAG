"""Regression tests for ordered, bounded Bilibili audio CDN fallback."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.bilibili.client import BilibiliClient


@pytest.mark.asyncio
async def test_get_audio_urls_returns_trusted_deduplicated_ordered_candidates(monkeypatch):
    client = object.__new__(BilibiliClient)
    client._cookies = {}
    monkeypatch.setattr(
        "app.services.bilibili.client.wbi_signer.sign",
        AsyncMock(return_value={"signed": "params"}),
    )
    payload = {
        "code": 0,
        "data": {
            "dash": {
                "audio": [
                    {
                        "bandwidth": 96_000,
                        "baseUrl": "https://high.bilivideo.com/audio.m4a",
                    },
                    {
                        "bandwidth": 64_000,
                        "baseUrl": "//primary.bilivideo.com/audio.m4a",
                        "backupUrl": [
                            "https://primary.bilivideo.com/audio.m4a",
                            "https://backup-one.bilivideo.com/audio.m4a",
                            "https://outside.example/audio.m4a",
                            "https://backup-two.bilivideo.com/audio.m4a",
                            "https://backup-three.bilivideo.com/audio.m4a",
                            "https://backup-four.bilivideo.com/audio.m4a",
                        ],
                    },
                ]
            }
        },
    }
    request = AsyncMock(return_value=SimpleNamespace(json=lambda: payload))
    monkeypatch.setattr(client, "_request", request)
    monkeypatch.setattr(client, "_get_client", lambda: object())

    urls = await client.get_audio_urls("BV1xx411c7mD", 1001)

    assert urls == [
        "https://primary.bilivideo.com/audio.m4a",
        "https://backup-one.bilivideo.com/audio.m4a",
        "https://backup-two.bilivideo.com/audio.m4a",
        "https://backup-three.bilivideo.com/audio.m4a",
    ]
    request.assert_awaited_once()


@pytest.mark.parametrize(
    ("status_code", "headers", "body"),
    [
        (
            206,
            {
                "content-length": "2048",
                "content-range": "bytes 0-2047/4096",
                "content-type": "audio/mp4",
            },
            b"a" * 2048,
        ),
        (
            200,
            {"content-length": "2048", "content-type": "audio/mp4"},
            b"a" * 1024,
        ),
    ],
)
@pytest.mark.asyncio
async def test_download_audio_rejects_incomplete_response_without_publishing_file(
    tmp_path, monkeypatch, status_code, headers, body,
):
    class FakeResponse:
        def __init__(self):
            self.status_code = status_code
            self.headers = headers

        async def aiter_bytes(self):
            yield body

    @asynccontextmanager
    async def stream(*_args, **_kwargs):
        yield FakeResponse()

    client = object.__new__(BilibiliClient)
    monkeypatch.setattr(client, "_get_client", lambda: SimpleNamespace(stream=stream))
    monkeypatch.setattr(client, "_get_semaphore", lambda: asyncio.Semaphore(1))
    target = tmp_path / "audio.m4a.part"

    succeeded = await client.download_audio_to_file(
        "https://cdn.bilivideo.com/audio.m4a", target,
    )

    assert succeeded is False
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []
