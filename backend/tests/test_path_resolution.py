"""
BUG-16 + MNT-12 回归测试：相对路径解析统一为"相对进程 CWD"，不手动拼 backend

`bilibili/client.py` 之前手动从 `__file__` 向上爬 3 层再拼相对路径，多拼出
一层 `app/`（变成 `backend/app/app/storage/...`）。`media_service.py` 的
`_get_audio_cache_dir` 也有类似的手动 `parents[]` climbing。两处都改成和
`settings_store.py._store_path()` 一样：直接使用配置里的相对路径，不做任何
`__file__` 爬升——不再有"爬几层"这种容易数错的手工计算。
"""
from __future__ import annotations

from pathlib import Path

from app.core.config import settings
from app.services import media_service
from app.services.bilibili.client import BilibiliClient
from app.services.settings_store import _store_path


def test_bilibili_state_path_has_no_duplicated_app_segment(monkeypatch):
    monkeypatch.setattr(BilibiliClient, "_load_state", lambda self: None)
    client = BilibiliClient()

    assert client._state_path == Path(settings.bilibili_state_path)
    parts = client._state_path.parts
    assert not any(a == "app" == b for a, b in zip(parts, parts[1:]))


def test_bilibili_state_path_uses_the_same_convention_as_settings_store(monkeypatch):
    """两处配置的相对路径都应该只是"原样相对 CWD"，不各自实现一套爬升逻辑。"""
    monkeypatch.setattr(BilibiliClient, "_load_state", lambda self: None)
    monkeypatch.setattr(settings, "bilibili_state_path", "app/storage/some_test_state.json")
    monkeypatch.setattr(settings, "api_settings_path", "app/storage/some_test_state.json")

    client = BilibiliClient()
    assert client._state_path == _store_path()


def test_audio_cache_dir_does_not_manually_climb_from_file(monkeypatch, tmp_path):
    absolute_override = tmp_path / "audio_cache"
    monkeypatch.setattr(media_service.settings, "audio_cache_dir", str(absolute_override))

    result = media_service._get_audio_cache_dir()

    assert result == absolute_override
    assert result.is_dir()
