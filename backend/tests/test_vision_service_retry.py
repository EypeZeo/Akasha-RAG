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
from app.core.model_gate import ModelCallAdmissionTimeout
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


def test_admission_timeout_propagates_without_matching_the_transient_retry(monkeypatch):
    """PR2B-4: ModelCallAdmissionTimeout 不匹配 except (ConnectionError, Timeout)，
    应该在第一次尝试就直接传播出去，不会被当成传输层异常再重试一次。"""
    call = Mock(return_value=_fake_response(200))
    monkeypatch.setattr(vision_module, "MultiModalConversation", SimpleNamespace(call=call))

    def raising_gate(kind):
        raise ModelCallAdmissionTimeout(kind, 30.0)

    monkeypatch.setattr(vision_module, "acquire_model_call_slot", raising_gate)

    with pytest.raises(ModelCallAdmissionTimeout):
        VisionService._call_vision_model([])
    call.assert_not_called()


def test_extract_text_from_images_skips_image_on_admission_timeout(monkeypatch):
    """PR2B-4: 单张图片的准入超时应该落进现有的逐图 try/except（跳过这张图、
    继续处理其余图片），不应该让整次图文提取失败。"""
    monkeypatch.setattr(settings, "dashscope_api_key", "fixture-secret")
    monkeypatch.setattr(vision_module, "safe_platform_image_url", lambda platform, url: url)

    service = VisionService()
    monkeypatch.setattr(service, "_download_trusted_image", lambda url, headers: b"\xff\xd8\xffjpeg-bytes")

    def raising_gate(kind):
        raise ModelCallAdmissionTimeout(kind, 30.0)

    monkeypatch.setattr(vision_module, "acquire_model_call_slot", raising_gate)

    result = service.extract_text_from_images(["http://x/img.jpg"], title="t")
    assert result == ""
