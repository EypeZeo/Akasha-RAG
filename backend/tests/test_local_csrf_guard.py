"""
本地 CSRF 防护（SEC-01）回归测试

覆盖：默认所有方法（含 GET）都要求约定请求头，带上正确的头能正常访问；
唯一豁免的是两个只读的浏览器原生下载/导出端点（<a>.click() / window.open()
结构上无法附带自定义头），按路径精确匹配，不按 HTTP 方法整体豁免——同样是
GET 但会写状态（/api/auth/platforms、/api/auth/bilibili/qrcode/poll）或
只是普通 fetch() 轮询（导出进度）的路由都不在豁免范围内（详见
app/core/security.py 模块文档）。
"""
import asyncio

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.core.security import require_local_client
from app.main import app


def test_get_route_without_header_is_rejected():
    """/api/auth/platforms 每次调用都会写库（unblock/record_account_state），
    且本来就是前端 fetch() 轮询、天然带头，不应该被豁免。"""
    with TestClient(app) as c:
        resp = c.get("/api/auth/platforms")
    assert resp.status_code == 403


def test_bilibili_qrcode_poll_without_header_is_rejected():
    """同样是会写状态的 GET 轮询端点，不应该被豁免。"""
    with TestClient(app) as c:
        resp = c.get("/api/auth/bilibili/qrcode/poll", params={"qrcode_key": "x"})
    assert resp.status_code == 403


def test_export_batch_progress_without_header_is_rejected():
    """导出进度轮询走 fetch()，不是原生导航，不应该被豁免——确认白名单是
    精确路径匹配，没有被 /export/batch 前缀误伤。"""
    with TestClient(app) as c:
        resp = c.get("/api/knowledge/export/batch/does-not-matter")
    assert resp.status_code == 403


def test_single_item_export_download_without_header_succeeds():
    """window.open() 发起的原生导航，无法带自定义头，必须豁免。"""
    with TestClient(app) as c:
        resp = c.get("/api/knowledge/export/does-not-exist")
    assert resp.status_code != 403


def test_batch_export_download_without_header_succeeds():
    """<a>.click() 发起的原生导航，无法带自定义头，必须豁免。"""
    with TestClient(app) as c:
        resp = c.get("/api/knowledge/export/batch/does-not-matter/download")
    assert resp.status_code != 403


def test_post_route_without_header_is_rejected():
    """状态变更路由（POST）仍然受保护。"""
    with TestClient(app) as c:
        resp = c.post("/api/favorites/sync")
    assert resp.status_code == 403


def test_post_route_with_wrong_header_is_rejected():
    with TestClient(app, headers={"X-Akasha-Client": "wrong"}) as c:
        resp = c.post("/api/favorites/sync")
    assert resp.status_code == 403


def test_get_route_with_client_header_succeeds():
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


def test_non_loopback_client_is_rejected_even_with_the_browser_header():
    request = Request({"type": "http", "method": "POST", "path": "/api/favorites/sync", "headers": [], "client": ("192.168.1.10", 50000)})
    with pytest.raises(HTTPException) as caught:
        asyncio.run(require_local_client(request, x_akasha_client="1"))
    assert caught.value.status_code == 403
