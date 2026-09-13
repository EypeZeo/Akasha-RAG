"""
BUG-12 回归测试：视觉模型调用需要真实超时 + 有限重试瞬态失败

`MultiModalConversation.call` 之前不传任何超时参数（SDK 默认 300s），且完全
不重试——一次网络抖动或 429/5xx 就直接放弃这张图。修复后传入可配置的
`request_timeout`，并对传输层异常和瞬态 HTTP 状态码各自最多重试一次；
鉴权/参数类的其它 4xx 不重试。
"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from app.core.config import settings
from app.services import vision_service as vision_module
from app.services.vision_service import VisionService


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr(vision_module.time, "sleep", lambda *_: None)


def _fake_response(status_code: int):
    return SimpleNamespace(status_code=status_code, output=None, message="")


def test_retries_once_after_a_timeout_then_succeeds(monkeypatch):
    call = Mock(side_effect=[requests.exceptions.Timeout(), _fake_response(200)])
    monkeypatch.setattr(vision_module, "MultiModalConversation", SimpleNamespace(call=call))

    response = VisionService._call_vision_model([])

    assert response.status_code == 200
    assert call.call_count == 2


def test_retries_once_on_a_transient_status_code_then_succeeds(monkeypatch):
    call = Mock(side_effect=[_fake_response(429), _fake_response(200)])
    monkeypatch.setattr(vision_module, "MultiModalConversation", SimpleNamespace(call=call))

    response = VisionService._call_vision_model([])

    assert response.status_code == 200
    assert call.call_count == 2


def test_does_not_retry_a_non_transient_client_error(monkeypatch):
    call = Mock(return_value=_fake_response(401))
    monkeypatch.setattr(vision_module, "MultiModalConversation", SimpleNamespace(call=call))

    response = VisionService._call_vision_model([])

    assert response.status_code == 401
    assert call.call_count == 1


def test_raises_after_the_single_retry_is_exhausted(monkeypatch):
    call = Mock(side_effect=[requests.exceptions.ConnectionError(), requests.exceptions.ConnectionError()])
    monkeypatch.setattr(vision_module, "MultiModalConversation", SimpleNamespace(call=call))

    with pytest.raises(requests.exceptions.ConnectionError):
        VisionService._call_vision_model([])
    assert call.call_count == 2


def test_call_passes_the_configured_request_timeout(monkeypatch):
    call = Mock(return_value=_fake_response(200))
    monkeypatch.setattr(vision_module, "MultiModalConversation", SimpleNamespace(call=call))

    VisionService._call_vision_model([{"role": "user", "content": []}])

    _args, kwargs = call.call_args
    assert kwargs["request_timeout"] == settings.vision_request_timeout_seconds
