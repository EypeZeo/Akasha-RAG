"""隔离 ASR 协议与故障收尾测试；不请求云端，不访问应用数据库。"""
from __future__ import annotations

import io
import json
import logging
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.services import asr_service as service_module
from app.services import asr_worker


@pytest.fixture
def service(monkeypatch, tmp_path):
    monkeypatch.setattr(service_module.settings, "dashscope_api_key", "unit-test-secret")
    monkeypatch.setattr(service_module.settings, "asr_model", "paraformer-v2")
    monkeypatch.setattr(service_module.settings, "asr_timeout_seconds", 180.0)
    monkeypatch.setattr(service_module.settings, "asr_max_audio_size_mb", 32.0)
    path = tmp_path / "音频 with spaces.mp3"
    path.write_bytes(b"ID3" + b"\0" * 64)
    return service_module.ASRService(), path


def test_parent_uses_private_stdin_and_legacy_realtime_model(service, monkeypatch):
    asr, path = service
    sentence = {"text": "完成识别", "start_ms": 100, "end_ms": 800, "lang": "zh"}

    def run(command, **kwargs):
        assert "unit-test-secret" not in " ".join(command)
        request = json.loads(kwargs["input"])
        assert request["audio_path"] == str(path.resolve())
        assert request["api_key"] == "unit-test-secret"
        assert request["model"] == "paraformer-realtime-v2"
        assert request["format"] == "mp3"
        assert kwargs["timeout"] == 180
        assert kwargs["stderr"] is subprocess.DEVNULL
        assert kwargs["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
        assert "DASHSCOPE_API_KEY" not in kwargs["env"]
        return SimpleNamespace(returncode=0, stdout=json.dumps({"segments": [sentence]}))

    monkeypatch.setattr(service_module.subprocess, "run", run)
    segments, language = asr.transcribe(path)
    assert segments[0].text == "完成识别"
    assert segments[0].start_ms == 100
    assert language == "zh"


@pytest.mark.parametrize("stdout", ["not JSON", "[]", "{}", '{"segments":{}}'])
def test_invalid_worker_protocol_is_actionable(service, monkeypatch, stdout):
    asr, path = service
    monkeypatch.setattr(
        service_module.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=stdout),
    )
    with pytest.raises(RuntimeError, match="返回格式无效"):
        asr.transcribe(path)


def test_worker_error_does_not_disclose_key(service, monkeypatch):
    asr, path = service
    monkeypatch.setattr(
        service_module.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"error": "403 rejected unit-test-secret"}),
        ),
    )
    with pytest.raises(RuntimeError, match="403 rejected") as error:
        asr.transcribe(path)
    assert "unit-test-secret" not in str(error.value)


def test_timeout_kills_and_reaps_real_process(service, monkeypatch):
    asr, path = service
    real_run = subprocess.run
    real_popen = subprocess.Popen
    children = []
    marker = path.with_suffix(".pid")

    def popen(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child

    def run(command, **kwargs):
        # Exercise the actual subprocess.run timeout cleanup with a non-network child.
        script = "import os,sys,time; from pathlib import Path; Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)"
        return real_run([command[0], "-c", script, str(marker)], **kwargs)

    monkeypatch.setattr(service_module.settings, "asr_timeout_seconds", 1.0)
    monkeypatch.setattr(service_module.subprocess, "Popen", popen)
    monkeypatch.setattr(service_module.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="工作进程已终止"):
        asr.transcribe(path)
    assert len(children) == 1
    assert int(marker.read_text()) == children[0].pid
    assert children[0].poll() is not None


def test_cancel_check_kills_asr_worker_immediately(service, monkeypatch):
    """Logout/cancel must not wait for the normal ASR timeout."""
    asr, path = service

    class HangingProcess:
        returncode = -9
        killed = False

        def communicate(self, **_kwargs):
            if not self.killed:
                raise subprocess.TimeoutExpired("asr", 0.25)
            return "", ""

        def kill(self):
            self.killed = True

    child = HangingProcess()
    monkeypatch.setattr(service_module.subprocess, "Popen", lambda *args, **kwargs: child)

    with pytest.raises(RuntimeError, match="已取消"):
        asr.transcribe(path, cancel_check=lambda: True)
    assert child.killed is True


@pytest.mark.parametrize("kind", ["empty", "oversize", "m4a", "missing_key", "wrong_model"])
def test_bad_inputs_fail_before_spawning(service, monkeypatch, kind):
    asr, path = service
    if kind == "empty":
        path.write_bytes(b"")
    elif kind == "oversize":
        monkeypatch.setattr(service_module.settings, "asr_max_audio_size_mb", 0.000001)
    elif kind == "m4a":
        replacement = path.with_suffix(".m4a")
        path.rename(replacement)
        path = replacement
    elif kind == "missing_key":
        monkeypatch.setattr(service_module.settings, "dashscope_api_key", "")
    elif kind == "wrong_model":
        monkeypatch.setattr(service_module.settings, "asr_model", "paraformer-realtime-8k-v2")

    def run(*args, **kwargs):
        pytest.fail("invalid input must not reach the paid SDK")

    monkeypatch.setattr(service_module.subprocess, "run", run)
    with pytest.raises(ValueError):
        asr.transcribe(path)


@pytest.mark.parametrize("field,value", [
    ("asr_timeout_seconds", 0), ("asr_timeout_seconds", 3601),
    ("asr_max_audio_size_mb", 0), ("asr_max_audio_size_mb", 257),
])
def test_asr_resource_limits_cannot_be_disabled(field, value):
    with pytest.raises(ValueError):
        Settings(_env_file=None, **{field: value})


def install_fake_recognition(monkeypatch, sentences, status=200):
    class FakeRecognition:
        def __init__(self, **kwargs):
            assert kwargs["api_key"] == "unit-test-secret"
            assert kwargs["request_timeout"] == 180

        def call(self, path):
            return SimpleNamespace(
                status_code=status, code="InvalidParameter", request_id="request-123",
                message="bad audio", get_sentence=lambda: sentences,
            )

    monkeypatch.setattr(asr_worker, "Recognition", FakeRecognition)
    return {
        "model": "paraformer-realtime-v2", "format": "mp3",
        "audio_path": "fake.mp3", "api_key": "unit-test-secret", "request_timeout": 180,
    }


def test_worker_extracts_finalized_sentences_and_ignores_empty_text(monkeypatch):
    request = install_fake_recognition(monkeypatch, [
        {"text": None}, {"text": "  "},
        {"text": "  你好  ", "begin_time": 0, "end_time": 1250},
    ])
    assert asr_worker.recognize(request) == [
        {"text": "你好", "start_ms": 0, "end_ms": 1250, "lang": "zh"},
    ]


@pytest.mark.parametrize("start,end", [(None, 100), (0, None), (-1, 100), (100, 10), (True, 10)])
def test_worker_rejects_invalid_citation_timestamps(monkeypatch, start, end):
    request = install_fake_recognition(monkeypatch, {
        "text": "不能生成错误时间戳", "begin_time": start, "end_time": end,
    })
    with pytest.raises(ValueError, match="时间戳"):
        asr_worker.recognize(request)


def test_worker_error_includes_request_id(monkeypatch):
    request = install_fake_recognition(monkeypatch, [], status=400)
    with pytest.raises(RuntimeError, match="request_id=request-123"):
        asr_worker.recognize(request)


def test_real_worker_starts_from_backend_without_loading_database():
    # An empty request fails validation before Recognition() or any network call.
    result = subprocess.run(
        [sys.executable, "-m", "app.services.asr_worker"],
        cwd=Path(__file__).resolve().parents[1],
        input="{}", capture_output=True, text=True, encoding="utf-8", timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0
    assert "KeyError" in json.loads(result.stdout)["error"]


def test_worker_stdout_remains_single_json_and_error_is_redacted(monkeypatch):
    def recognize(request):
        print("unexpected SDK output")
        raise RuntimeError("rejected unit-test-secret")

    monkeypatch.setattr(asr_worker, "recognize", recognize)
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"api_key":"unit-test-secret"}'))
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    previous_disable = logging.root.manager.disable
    try:
        asr_worker.main()
    finally:
        logging.disable(previous_disable)
    result = json.loads(stdout.getvalue())
    assert result["error"] == "RuntimeError: rejected [redacted]"
