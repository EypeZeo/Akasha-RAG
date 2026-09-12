"""
PERF-08 回归测试：本地设置文件按 mtime 缓存，写入后立即失效

测试目标是 `secure_storage.read_json` 的调用次数——PR#6 已经把底层文件
读取从裸 `Path.read_text`/`json.loads` 换成了 DPAPI 保护的 `read_json`，
缓存必须盯着这一层的调用次数，不是一个已经不存在的实现细节。
"""
from unittest.mock import MagicMock

import pytest

from app.services import settings_store


@pytest.fixture(autouse=True)
def _reset_cache():
    settings_store._cache = None
    yield
    settings_store._cache = None


@pytest.fixture
def store_path(tmp_path, monkeypatch):
    path = tmp_path / "api_settings.json"
    monkeypatch.setattr(settings_store.settings, "api_settings_path", str(path))
    return path


def test_repeated_reads_hit_the_cache_when_file_is_unchanged(store_path, monkeypatch):
    read_json = MagicMock(return_value={"dashscope_api_key": "sk-abc"})
    monkeypatch.setattr(settings_store, "read_json", read_json)
    # storage_signature reflects real filesystem state; write the file for
    # real so the (exists, mtime_ns) signature is stable across calls.
    store_path.write_text("{}", encoding="utf-8")

    settings_store._read()
    settings_store._read()
    settings_store._read()

    assert read_json.call_count == 1


def test_write_invalidates_the_cache(store_path, monkeypatch):
    read_json = MagicMock(return_value={"dashscope_api_key": "sk-abc"})
    write_json = MagicMock()
    monkeypatch.setattr(settings_store, "read_json", read_json)
    monkeypatch.setattr(settings_store, "write_json", write_json)
    store_path.write_text("{}", encoding="utf-8")

    data = settings_store._read()
    assert read_json.call_count == 1

    data.dashscope_api_key = "sk-new"
    settings_store._write(data)
    settings_store._read()

    assert read_json.call_count == 2


def test_mtime_change_invalidates_the_cache(store_path, monkeypatch):
    read_json = MagicMock(return_value={"dashscope_api_key": "sk-abc"})
    monkeypatch.setattr(settings_store, "read_json", read_json)
    store_path.write_text("{}", encoding="utf-8")

    settings_store._read()
    assert read_json.call_count == 1

    # Simulate an external process touching the file (mtime changes) without
    # going through settings_store's own _write().
    import os
    import time
    time.sleep(0.01)
    os.utime(store_path, None)

    settings_store._read()
    assert read_json.call_count == 2


def test_read_returns_a_deep_copy_callers_cannot_taint_the_cache(store_path, monkeypatch):
    monkeypatch.setattr(settings_store, "read_json", MagicMock(return_value={"dashscope_api_key": "sk-abc"}))
    store_path.write_text("{}", encoding="utf-8")

    first = settings_store._read()
    first.dashscope_api_key = "tampered"
    second = settings_store._read()

    assert second.dashscope_api_key == "sk-abc"
