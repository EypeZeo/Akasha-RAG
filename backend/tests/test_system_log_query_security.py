from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.routes import system
from app.main import app


def test_log_endpoint_uses_only_fixed_log_patterns(tmp_path, monkeypatch):
    monkeypatch.setattr(system, "LOG_DIR", tmp_path)
    (tmp_path / "all_20260912.log").write_text("one\ntwo\n", encoding="utf-8")
    with TestClient(app, headers={"X-Akasha-Client": "1"}) as client:
        response = client.get("/api/system/logs?log_type=all&lines=1")
    assert response.status_code == 200
    assert response.json()["lines"] == ["two"]


def test_log_endpoint_rejects_glob_injection_and_unbounded_lines():
    with TestClient(app, headers={"X-Akasha-Client": "1"}) as client:
        assert client.get("/api/system/logs?log_type=../*.log").status_code == 422
        assert client.get("/api/system/logs?log_type=all&lines=0").status_code == 422
        assert client.get("/api/system/logs?log_type=all&lines=1001").status_code == 422
