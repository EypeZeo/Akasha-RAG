"""Exercise actual diagnostic URLs without starting a live backend/database."""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes import metrics
from app.core.security import require_local_client
from app.db.base import Base
from app.db.session import get_db


def _app():
    app = FastAPI()
    api = APIRouter(prefix="/api", dependencies=[Depends(require_local_client)])
    api.include_router(metrics.router)
    app.include_router(api)
    return app


def test_actual_metrics_url_and_mode_gate(monkeypatch):
    app = _app()
    client = TestClient(app, headers={"X-Akasha-Client": "1"})
    assert client.get("/api/metrics/health").status_code == 200
    assert client.get("/api/api/metrics/health").status_code == 404
    monkeypatch.setattr(metrics.settings, "developer_mode", False)
    assert client.get("/api/metrics/database").status_code == 403


@pytest.mark.parametrize("proxy, expected", [
    ("http://alice:secret@127.0.0.1:7890", "http://127.0.0.1:7890"),
    ("socks5://alice:secret@[::1]:7890", "socks5://[::1]:7890"),
])
def test_network_metrics_do_not_reveal_proxy_credentials(monkeypatch, proxy, expected):
    monkeypatch.setattr(metrics.settings, "developer_mode", True)
    monkeypatch.setattr(metrics, "detect_network_proxy", lambda **kwargs: proxy)
    response = TestClient(_app(), headers={"X-Akasha-Client": "1"}).get("/api/metrics/network")
    assert response.status_code == 200
    assert response.json()["proxy"]["url"] == expected
    assert "alice" not in response.text and "secret" not in response.text


def test_database_metrics_use_current_entities(monkeypatch):
    from app.models import entities  # noqa: F401 -- register tables before create_all

    monkeypatch.setattr(metrics.settings, "developer_mode", True)
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    app = _app()

    def database():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = database
    response = TestClient(app, headers={"X-Akasha-Client": "1"}).get("/api/metrics/database")
    assert response.status_code == 200
    assert response.json()["tables"] == {
        "source_accounts": 0, "favorite_collections": 0, "content_items": 0,
        "collection_items": 0, "ingestion_items": 0,
    }
    engine.dispose()


@pytest.mark.asyncio
async def test_system_sampling_does_not_block_event_loop(monkeypatch):
    import httpx

    monkeypatch.setattr(metrics.settings, "developer_mode", True)
    started = threading.Event()
    release = threading.Event()
    real_cpu_percent = metrics.psutil.cpu_percent

    def blocking_cpu_percent(**kwargs):
        started.set()
        assert release.wait(timeout=5)
        return real_cpu_percent(interval=None)

    monkeypatch.setattr(metrics.psutil, "cpu_percent", blocking_cpu_percent)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()), base_url="http://localhost", headers={"X-Akasha-Client": "1"}) as client:
        request = asyncio.create_task(client.get("/api/metrics/system"))
        assert await asyncio.to_thread(started.wait, 5)
        try:
            await asyncio.sleep(0)
            assert not request.done()
        finally:
            release.set()
        assert (await request).status_code == 200


@pytest.mark.parametrize("failure", [FileNotFoundError, PermissionError])
def test_cache_metrics_tolerate_concurrent_file_cleanup(monkeypatch, tmp_path, failure):
    from app.db import session as db_session

    monkeypatch.setattr(metrics.settings, "developer_mode", True)
    audio = tmp_path / "audio"
    audio.mkdir()
    kept = audio / "kept.mp3"
    kept.write_bytes(b"a" * 1048576)
    removed = audio / "removed.mp3"
    removed.write_bytes(b"b")
    chroma = tmp_path / "chroma"
    chroma.mkdir()
    (chroma / "index.bin").write_bytes(b"c" * 2097152)
    monkeypatch.setattr(metrics.settings, "audio_cache_dir", str(audio))
    monkeypatch.setattr(metrics.settings, "chroma_persist_dir", str(chroma))
    database = tmp_path / "db.sqlite"
    database.write_bytes(b"d" * 1048576)
    wal = Path(str(database) + "-wal")
    wal.write_bytes(b"e")
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    monkeypatch.setattr(db_session, "engine", engine)
    original_stat = Path.stat

    def concurrent_stat(path, *args, **kwargs):
        if path in (removed, wal):
            raise failure("file is being cleaned up")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", concurrent_stat)
    try:
        response = TestClient(_app(), headers={"X-Akasha-Client": "1"}).get("/api/metrics/cache")
        assert response.status_code == 200
        data = response.json()
        assert data["audio_cache"]["file_count"] == 1
        assert data["audio_cache"]["size_mb"] == 1.0
        assert data["vector_db"]["size_mb"] == 2.0
        assert data["sqlite"] == {"db_size_mb": 1.0, "wal_size_mb": 0.0, "total_size_mb": 1.0}
    finally:
        engine.dispose()


def test_cache_metrics_tolerate_unreadable_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(metrics.settings, "developer_mode", True)
    monkeypatch.setattr(metrics.settings, "audio_cache_dir", str(tmp_path))
    original_rglob = Path.rglob

    def unreadable(path, *args, **kwargs):
        if path == tmp_path:
            raise PermissionError("directory is temporarily unavailable")
        return original_rglob(path, *args, **kwargs)

    monkeypatch.setattr(Path, "rglob", unreadable)
    response = TestClient(_app(), headers={"X-Akasha-Client": "1"}).get("/api/metrics/cache")
    assert response.status_code == 200
    assert response.json()["audio_cache"]["file_count"] == 0
