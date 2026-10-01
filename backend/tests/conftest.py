"""Never use desktop credentials or persistent data during pytest collection.

Modules create collectors/database engines at import time, before fixtures run.
Install process-local storage overrides before importing application modules.
"""
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest


_TEST_STORAGE = TemporaryDirectory(prefix='akasha-pytest-', ignore_cleanup_errors=True)
_ROOT = Path(_TEST_STORAGE.name)
_PROFILE = _ROOT / 'playwright_user_data'
_PROFILE.mkdir()
# The collector's legacy-path discovery prefers directories with a state file.
# Seed an empty session so importing it cannot discover a real desktop profile.
(_PROFILE / 'state.json').write_text('{"cookies": [], "origins": []}', encoding='utf-8')

for _name, _value in {
    'DATABASE_URL': f'sqlite:///{(_ROOT / "test.db").as_posix()}',
    'CHROMA_PERSIST_DIR': str(_ROOT / 'chroma'),
    'AUDIO_CACHE_DIR': str(_ROOT / 'audio_cache'),
    'BILIBILI_AUDIO_CACHE_DIR': str(_ROOT / 'audio_cache' / 'bilibili'),
    'PLAYWRIGHT_USER_DATA_DIR': str(_PROFILE),
    'API_SETTINGS_PATH': str(_ROOT / 'api_settings.json'),
    'BILIBILI_STATE_PATH': str(_ROOT / 'bilibili_state.json'),
    'ZHIHU_STATE_PATH': str(_ROOT / 'zhihu_state.json'),
    'DEEPSEEK_API_KEY': 'test-only-not-a-real-key',
    'DASHSCOPE_API_KEY': 'test-only-not-a-real-key',
}.items():
    os.environ[_name] = _value


@pytest.fixture(autouse=True)
def isolate_profile_discovery(monkeypatch, tmp_path_factory):
    # Logout tests remove their profile directory. Later collectors must not
    # fall back to the application's legacy profile when the empty seed is gone.
    from app.services import douyin_collector
    # Keep this independent of tmp_path: media tests assert that their own
    # cache directories contain only the files produced by the operation.
    isolated_profile = tmp_path_factory.mktemp('browser-profile')
    monkeypatch.setattr(douyin_collector, '_find_user_data_dir', lambda: isolated_profile)
