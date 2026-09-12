"""Launcher i18n language resolution — env var priority, locale probe, fallback."""
import importlib
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _resolve(monkeypatch, *, system_locale=(None, None), **env):
    """Reimport launcher_i18n with a controlled environment and return resolve_lang().

    The host's real system locale is neutralized so tests exercise the documented
    priority (env var > LANG probe > 'en') deterministically on any machine.
    """
    for key in ("AKASHA_LANG", "LANG"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr("locale.getlocale", lambda *a, **k: system_locale)
    mod = importlib.reload(importlib.import_module("launcher_i18n"))
    return mod.resolve_lang()


@pytest.mark.parametrize(
    "env, expected",
    [
        ({"AKASHA_LANG": "ja"}, "ja"),
        ({"AKASHA_LANG": "zh-CN"}, "zh"),          # 2-letter prefix match
        ({"AKASHA_LANG": "klingon"}, None),        # unknown -> falls through to probe
    ],
)
def test_env_var_priority(monkeypatch, env, expected):
    lang = _resolve(monkeypatch, **env)
    if expected is None:
        assert lang in {"zh", "en", "ja", "fr", "de", "ko", "ru", "hi"}
    else:
        assert lang == expected


def test_locale_probe_via_lang_env(monkeypatch):
    assert _resolve(monkeypatch, LANG="zh_CN.UTF-8") == "zh"
    assert _resolve(monkeypatch, LANG="ko_KR.UTF-8") == "ko"


def test_default_fallback_is_english(monkeypatch):
    assert _resolve(monkeypatch, LANG="") == "en"


def test_system_locale_probe(monkeypatch):
    assert _resolve(monkeypatch, system_locale=("Chinese (Simplified)_China", "936")) == "zh"


def test_every_key_is_translated_into_all_languages(monkeypatch):
    """Keyset parity across all 8 locales.

    Replaces the old preflight-specific check: start.bat no longer renders any
    localized string (bootstrap.ps1 runs before the venv exists and carries its
    own EN/ZH table), so the four ``preflight_*`` keys were removed in v0.7.5.
    Guarding the whole table is strictly stronger than guarding those four.
    """
    mod = importlib.reload(importlib.import_module("launcher_i18n"))
    assert mod._STRINGS, "launcher_i18n string table is empty"
    for key, entry in mod._STRINGS.items():
        missing = set(mod.SUPPORTED) - set(entry)
        extra = set(entry) - set(mod.SUPPORTED)
        assert not missing, f"{key} is missing translations: {sorted(missing)}"
        assert not extra, f"{key} has unknown languages: {sorted(extra)}"


def test_launcher_rejects_requested_unsupported_platforms():
    launcher = importlib.import_module("launcher")

    assert "Linux" in launcher.unsupported_platform_message(platform="linux")
    assert "macOS" in launcher.unsupported_platform_message(platform="darwin")

    # Floor is Windows 10 and must stay in lockstep with bootstrap.ps1: the
    # runtimes the one-click setup installs do not support 7/8/8.1.
    for rejected in ((6, 0), (6, 1), (6, 2), (6, 3)):
        message = launcher.unsupported_platform_message(platform="win32", windows_version=rejected)
        assert message is not None, f"Windows {rejected} should be rejected"
        assert "Windows 10" in message

    assert launcher.unsupported_platform_message(platform="win32", windows_version=(10, 0)) is None
    assert launcher.unsupported_platform_message(platform="win32", windows_version=(11, 0)) is None
