"""Project-local Chromium discovery.

Playwright renamed the Windows payload directory from ``chrome-win`` to
``chrome-win64``.  ``douyin_collector`` matched only the old name, so on a clean
machine it returned ``None`` and silently fell back to a *system* Chrome/Edge
channel -- invisible on a developer box that has Chrome, broken on the fresh
Windows install that ``start.bat`` is supposed to serve.

These tests pin the behaviour so a future Playwright rename fails loudly here
instead of degrading into a system-browser fallback at runtime.
"""
import re
from pathlib import Path

import pytest

from app.services import douyin_collector, douyin_media_resolver

#: Layout names Playwright has actually shipped on Windows.
KNOWN_PAYLOAD_DIRS = ("chrome-win", "chrome-win64")


def _make_install(root: Path, payload_dir: str, build: str = "chromium-1234") -> Path:
    """Create a fake Playwright browser install and return the chrome.exe path."""
    exe = root / build / payload_dir / "chrome.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"MZ")
    return exe


@pytest.mark.parametrize("payload_dir", KNOWN_PAYLOAD_DIRS)
def test_collector_finds_chromium_for_every_known_layout(tmp_path, monkeypatch, payload_dir):
    expected = _make_install(tmp_path, payload_dir)
    monkeypatch.setattr(
        douyin_collector.settings, "playwright_browsers_path", str(tmp_path), raising=False
    )
    assert douyin_collector._find_project_chromium_executable() == expected


def test_collector_returns_none_when_nothing_is_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        douyin_collector.settings, "playwright_browsers_path", str(tmp_path), raising=False
    )
    assert douyin_collector._find_project_chromium_executable() is None


def test_collector_prefers_the_newest_build(tmp_path, monkeypatch):
    _make_install(tmp_path, "chrome-win64", build="chromium-1000")
    newest = _make_install(tmp_path, "chrome-win64", build="chromium-1234")
    monkeypatch.setattr(
        douyin_collector.settings, "playwright_browsers_path", str(tmp_path), raising=False
    )
    assert douyin_collector._find_project_chromium_executable() == newest


@pytest.mark.parametrize("payload_dir", KNOWN_PAYLOAD_DIRS)
def test_media_resolver_uses_the_project_chromium_not_a_system_channel(
    tmp_path, monkeypatch, payload_dir
):
    """The resolver must launch the bundled binary, never fall through to a channel."""
    expected = _make_install(tmp_path, payload_dir)
    monkeypatch.setattr(
        douyin_media_resolver.settings, "playwright_browsers_path", str(tmp_path), raising=False
    )
    kwargs = douyin_media_resolver._browser_launch_kwargs()
    assert kwargs.get("executable_path") == str(expected)
    assert "channel" not in kwargs


def test_both_resolvers_cover_the_same_layouts():
    """douyin_media_resolver duplicates the globs on purpose -- keep them in sync.

    It cannot import douyin_collector without instantiating that module's
    singleton, so the two lists are compared here instead.
    """
    source = Path(douyin_media_resolver.__file__).read_text(encoding="utf-8")
    resolver_globs = set(re.findall(r'"(chromium-\*/[^"]+/chrome\.exe)"', source))
    assert resolver_globs == set(douyin_collector._CHROMIUM_GLOBS)


def test_known_layouts_are_all_represented():
    """Guards against someone trimming the glob list back down to one entry."""
    covered = {g.split("/")[1] for g in douyin_collector._CHROMIUM_GLOBS}
    assert covered == set(KNOWN_PAYLOAD_DIRS)
