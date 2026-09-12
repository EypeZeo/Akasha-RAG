"""
多平台 API 路由端到端测试
"""
import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.adapters.base import AuthStatus, QRCodeInfo, QRCheckResult
from app.services.bilibili.client import bilibili_client


@pytest.fixture
def client():
    with TestClient(app, headers={"X-Akasha-Client": "1"}) as c:
        yield c


def test_auth_platforms_endpoint(client):
    """测试 /api/auth/platforms 返回所有平台状态"""
    resp = client.get("/api/auth/platforms")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    platforms = {p["platform"]: p for p in data["platforms"]}
    assert "douyin" in platforms
    assert "bilibili" in platforms
    assert platforms["douyin"]["name"] == "抖音"
    assert platforms["bilibili"]["name"] == "哔哩哔哩"


@pytest.mark.asyncio
async def test_bilibili_auth_flow(client):
    """测试 B 站二维码生成与状态轮询 API"""
    mock_qr = QRCodeInfo(
        platform="bilibili",
        qrcode_key="test_key_123",
        qrcode_url="https://passport.bilibili.com/h5-app/passport/login/scan?qrcode_key=test_key_123",
        qrcode_image_base64="fake_base64",
        expires_in=180,
    )

    with patch("app.services.bilibili.client.bilibili_client.generate_qrcode", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = mock_qr
        resp = client.post("/api/auth/bilibili/qrcode/generate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["qrcode_key"] == "test_key_123"
        assert data["data"]["qrcode_image_base64"] == "fake_base64"

    mock_poll = QRCheckResult(
        platform="bilibili",
        status="confirmed",
        message="扫码登录成功",
        account_id="123456",
        nickname="测试UP主",
        avatar_url="https://avatar.test.com/face.jpg",
    )
    with patch("app.services.bilibili.client.bilibili_client.poll_qrcode_status", new_callable=AsyncMock) as mock_p:
        mock_p.return_value = mock_poll
        resp = client.get("/api/auth/bilibili/qrcode/poll?qrcode_key=test_key_123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["status"] == "confirmed"
        assert data["nickname"] == "测试UP主"


def test_bilibili_logout_endpoint(client):
    """测试 B 站退出登录接口"""
    with patch("app.services.bilibili.client.bilibili_client.clear_state") as mock_clear:
        resp = client.post("/api/auth/bilibili/logout")
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        mock_clear.assert_called_once()


def test_logout_all_endpoint_clears_both_platforms(client):
    with patch("app.api.routes.auth.collector.logout", return_value=(True, "已退出登录")) as mock_douyin, \
         patch("app.api.routes.auth.bilibili_client.clear_state") as mock_bilibili:
        resp = client.post("/api/auth/logout-all")

    assert resp.status_code == 200
    assert resp.json()["success"] is True
    mock_douyin.assert_called_once()
    mock_bilibili.assert_called_once()


def test_favorites_collections_platform_filter(client):
    """测试收藏夹列表平台过滤"""
    resp_all = client.get("/api/favorites/collections?platform=all")
    assert resp_all.status_code == 200
    assert resp_all.json()["success"] is True

    resp_bili = client.get("/api/favorites/collections?platform=bilibili")
    assert resp_bili.status_code == 200
    assert resp_bili.json()["success"] is True

    resp_douyin = client.get("/api/favorites/collections?platform=douyin")
    assert resp_douyin.status_code == 200
    assert resp_douyin.json()["success"] is True


def test_favorites_videos_endpoint_reports_kind_counts(client):
    """收藏夹作品列表须返回整栏 video_count / note_count，供前端决定是否隐藏分类筛选行。"""
    resp = client.get("/api/favorites/collections/all/videos?platform=bilibili")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "video_count" in data
    assert "note_count" in data
    assert isinstance(data["video_count"], int)
    assert isinstance(data["note_count"], int)


def test_favorites_sync_rejects_unknown_platform(client):
    resp = client.post("/api/favorites/sync?platform=unknown")
    assert resp.status_code == 200
    assert resp.json()["success"] is False


def test_knowledge_pending_platform_filter(client):
    """测试待入库内容平台过滤"""
    resp = client.get("/api/knowledge/pending?platform=bilibili")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "video_count" in data
    assert "note_count" in data
    assert "has_more" in data


@pytest.mark.asyncio
async def test_bilibili_generate_qrcode_service():
    """验证 generate_qrcode 生成的 Base64 图片能正确解码为有效 PNG"""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "code": 0,
        "data": {
            "qrcode_key": "real_mock_key_456",
            "url": "https://passport.bilibili.com/h5-app/passport/login/scan?qrcode_key=real_mock_key_456",
        },
    }

    mock_http_client = MagicMock()
    mock_http_client.get = AsyncMock(return_value=mock_resp)

    with patch.object(bilibili_client, "_get_client", return_value=mock_http_client):
        qr = await bilibili_client.generate_qrcode()
        assert qr.qrcode_key == "real_mock_key_456"
        assert not qr.qrcode_image_base64.startswith("data:")
        # 验证 Base64 解码与 PNG 魔数
        raw_png = base64.b64decode(qr.qrcode_image_base64)
        assert raw_png[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.asyncio
async def test_douyin_qrcode_endpoints(client):
    """测试抖音登录二维码生成、刷新、独立窗口展开以及状态返回"""
    fake_qr = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    
    with patch("app.api.routes.auth.collector.start_login") as mock_start, \
         patch("app.api.routes.auth.collector.wait_for_qrcode", new_callable=AsyncMock) as mock_wait, \
         patch("app.api.routes.auth.collector.get_qrcode", return_value=fake_qr), \
         patch("app.api.routes.auth.collector.refresh_qrcode", return_value=fake_qr), \
         patch("app.api.routes.auth.collector.show_browser_window", return_value=True):
        
        mock_wait.return_value = fake_qr

        # 1. 生成二维码
        resp = client.post("/api/auth/douyin/qrcode/generate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["qrcode_image_base64"] == fake_qr
        assert data["data"]["expires_in"] == 120

        # 2. 状态查询附带二维码
        resp_status = client.get("/api/auth/douyin/login/status")
        assert resp_status.status_code == 200
        data_status = resp_status.json()
        assert data_status["qrcode_image_base64"] == fake_qr

        # 3. 刷新二维码
        resp_refresh = client.post("/api/auth/douyin/qrcode/refresh")
        assert resp_refresh.status_code == 200
        data_refresh = resp_refresh.json()
        assert data_refresh["success"] is True
        assert data_refresh["data"]["qrcode_image_base64"] == fake_qr

        # 4. 独立窗口展开
        resp_win = client.post("/api/auth/douyin/window/show")
        assert resp_win.status_code == 200
        assert resp_win.json()["success"] is True


def test_sync_favorites_handles_connect_timeout_gracefully(client):
    """验证遇到 ConnectTimeout 异常时返回友好的中文提示而非 ConnectTimeout: """
    import httpx
    from app.services.worker import worker
    worker.unblock_platform("bilibili")
    with patch("app.services.favorites_service.favorites_service.sync_from_bilibili", side_effect=httpx.ConnectTimeout("")):
        resp = client.post("/api/favorites/sync?platform=bilibili")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "连接平台服务器超时" in data["message"]
        assert "ConnectTimeout: " not in data["message"]


@pytest.mark.asyncio
async def test_bilibili_client_request_retries_on_connect_timeout():
    """验证 BilibiliClient._request 遇到网络超时自动重试并在重试成功后返回数据"""
    import httpx
    from app.services.bilibili.client import BilibiliClient
    from unittest.mock import MagicMock

    test_client = BilibiliClient()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"code": 0, "data": {"test": "ok"}}

    call_count = 0

    def mock_get(url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.ConnectTimeout("Connection timed out")
        return mock_resp

    mock_http = MagicMock()
    mock_http.get = AsyncMock(side_effect=mock_get)
    mock_http.is_closed = False
    mock_http.aclose = AsyncMock()

    with patch.object(test_client, "_get_client", return_value=mock_http):
        resp = await test_client._request("GET", "https://api.bilibili.com/test", max_retries=2)
        assert resp.json()["data"]["test"] == "ok"
        assert call_count == 2


def test_sync_favorites_summary_message(client):
    """验证同步接口返回符合规范的 summary_message"""
    from app.services.worker import worker
    worker.unblock_platform("bilibili")
    worker.unblock_platform("douyin")

    mock_bili = {
        "platform": "bilibili",
        "videos_total": 50,
        "invalid_count": 3,
        "added_videos": 5,
        "removed_videos": 0,
    }
    with patch("app.services.favorites_service.favorites_service.sync_from_bilibili", AsyncMock(return_value=mock_bili)):
        resp = client.post("/api/favorites/sync?platform=bilibili")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "已同步 50 个视频" in data["summary_message"]
        assert "来自哔哩哔哩平台 50 个视频" in data["summary_message"]
        assert "已失效视频 3 个无法同步" in data["summary_message"]
        assert "同步已完成" in data["summary_message"]



