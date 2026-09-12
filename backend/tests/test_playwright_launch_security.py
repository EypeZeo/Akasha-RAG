from __future__ import annotations

from app.services import douyin_collector


def test_collector_never_disables_chromium_sandbox(monkeypatch):
    monkeypatch.setattr(douyin_collector, "_find_project_chromium_executable", lambda: None)
    monkeypatch.setattr(douyin_collector.sys, "platform", "win32")
    monkeypatch.setattr(douyin_collector.settings, "playwright_browser_channel", "chromium")
    collector = object.__new__(douyin_collector.DouyinCollector)

    assert "--no-sandbox" not in collector._browser_launch_kwargs()["args"]
