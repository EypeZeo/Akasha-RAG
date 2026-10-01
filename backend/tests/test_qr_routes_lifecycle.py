"""QR API handlers wait for worker results without issuing browser calls."""
from unittest.mock import AsyncMock, Mock

import pytest

from app.api.routes import auth


@pytest.mark.asyncio
async def test_rejected_douyin_start_never_waits_or_restarts_worker(monkeypatch):
    fake = Mock(status='pending', message='cleanup pending')
    fake.wait_for_login_retirement = AsyncMock(return_value=True)
    fake.start_login.return_value = (False, 'cleanup pending')
    fake.wait_for_qrcode = AsyncMock()
    monkeypatch.setattr(auth, 'collector', fake)
    result = await auth.douyin_qrcode_generate()
    assert result['success'] is False
    assert result['data']['qrcode_image_base64'] == ''
    fake.wait_for_qrcode.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('platform', ['douyin', 'zhihu'])
async def test_failed_retirement_does_not_spawn_a_new_login(monkeypatch, platform):
    fake = Mock()
    fake.wait_for_login_retirement = AsyncMock(return_value=False)
    monkeypatch.setattr(auth, 'collector' if platform == 'douyin' else 'zhihu_collector', fake)
    result = await (auth.douyin_qrcode_generate() if platform == 'douyin' else auth.zhihu_login_start())
    assert result['success'] is False
    fake.start_login.assert_not_called()


@pytest.mark.asyncio
async def test_refresh_waits_asynchronously_for_worker_snapshot(monkeypatch):
    fake = Mock(status='pending', message='waiting')
    fake.refresh_qrcode.return_value = None
    fake.wait_for_qrcode = AsyncMock(return_value='new-qr')
    monkeypatch.setattr(auth, 'collector', fake)
    result = await auth.douyin_qrcode_refresh()
    assert result['success'] is True
    assert result['data']['qrcode_image_base64'] == 'new-qr'
    fake.wait_for_qrcode.assert_awaited_once_with(timeout=30.0)


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['syncing', 'logged_in', 'expired', 'failed'])
async def test_refresh_does_not_restart_a_completed_or_retired_login(monkeypatch, status):
    fake = Mock(status=status)
    fake.refresh_qrcode.return_value = None
    fake.wait_for_qrcode = AsyncMock()
    monkeypatch.setattr(auth, 'collector', fake)
    result = await auth.douyin_qrcode_refresh()
    assert result['success'] is False
    fake.wait_for_qrcode.assert_not_awaited()
