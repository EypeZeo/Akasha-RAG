"""
本地 CSRF 防护（SEC-01）回归测试

覆盖：不带约定请求头一律 403；带上正确的头能正常访问。挑几个有代表性的
路由（不同 router、不同 HTTP method），不需要每个路由都测——依赖是挂在
`api_router` 级别的，测通几个就能确认这套机制本身在生效。
"""
from fastapi.testclient import TestClient

from app.main import app


def test_request_without_client_header_is_rejected():
    with TestClient(app) as c:
        resp = c.get("/api/auth/platforms")
    assert resp.status_code == 403


def test_request_with_wrong_client_header_is_rejected():
    with TestClient(app, headers={"X-Akasha-Client": "wrong"}) as c:
        resp = c.get("/api/auth/platforms")
    assert resp.status_code == 403


def test_request_with_client_header_succeeds():
    with TestClient(app, headers={"X-Akasha-Client": "1"}) as c:
        resp = c.get("/api/auth/platforms")
    assert resp.status_code == 200


def test_post_route_without_header_is_rejected():
    """状态变更路由（POST）同样受保护，不只是 GET。"""
    with TestClient(app) as c:
        resp = c.post("/api/favorites/sync")
    assert resp.status_code == 403


def test_root_health_check_is_not_gated():
    """依赖挂在 /api 前缀的 router 上，根路径的健康检查不受影响。"""
    with TestClient(app) as c:
        resp = c.get("/")
    assert resp.status_code == 200
