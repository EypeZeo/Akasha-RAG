"""Exercise actual diagnostic URLs without starting a live backend/database."""
from __future__ import annotations

import asyncio
import threading

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
