"""
本地 CSRF 防护（SEC-01）回归测试

覆盖：状态变更请求（POST/PUT/PATCH/DELETE）不带约定请求头一律 403、带上正确
的头能正常访问；GET/HEAD 请求不要求这个头，因为原生浏览器下载/导出
（<a>.click() / window.open()）无法附带自定义请求头，而 GET 本身无副作用
不构成 CSRF 攻击面（详见 app/core/security.py 模块文档）。
"""
from fastapi.testclient import TestClient

from app.main import app


def test_get_route_without_header_succeeds():
    """GET 无副作用，不应被这套本地 CSRF 头挡住（对应导出/下载场景）。"""
    with TestClient(app) as c:
        resp = c.get("/api/auth/platforms")
    assert resp.status_code == 200


def test_post_route_without_header_is_rejected():
    """状态变更路由（POST）仍然受保护。"""
    with TestClient(app) as c:
        resp = c.post("/api/favorites/sync")
    assert resp.status_code == 403


def test_post_route_with_wrong_header_is_rejected():
    with TestClient(app, headers={"X-Akasha-Client": "wrong"}) as c:
        resp = c.post("/api/favorites/sync")
    assert resp.status_code == 403


def test_post_route_with_client_header_succeeds():
    with TestClient(app, headers={"X-Akasha-Client": "1"}) as c:
        resp = c.get("/api/auth/platforms")
    assert resp.status_code == 200


def test_put_route_without_header_is_rejected():
    with TestClient(app) as c:
        resp = c.put("/api/settings/dashscope-key", json={"api_key": "sk-test"})
    assert resp.status_code == 403


def test_delete_route_without_header_is_rejected():
    with TestClient(app) as c:
        resp = c.delete("/api/knowledge/videos/does-not-matter")
    assert resp.status_code == 403


def test_root_health_check_is_not_gated():
    """依赖挂在 /api 前缀的 router 上，根路径的健康检查不受影响。"""
    with TestClient(app) as c:
        resp = c.get("/")
    assert resp.status_code == 200
