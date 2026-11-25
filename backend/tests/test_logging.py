"""Services must reach rotating logs even when console logging is disabled."""
import logging
from io import StringIO

import pytest
from loguru import logger

from app.core.logging import _ApplicationLogHandler, _console_format


def test_stdlib_service_messages_and_exceptions_reach_loguru():
    records = []
    sink = logger.add(lambda message: records.append(message.record), diagnose=False)
    standard = logging.getLogger("test_douyin_service_bridge")
    bridge = _ApplicationLogHandler("en")
    standard.addHandler(bridge)
    original = standard.level
    standard.setLevel(logging.INFO)
    try:
        standard.info("ASR start %s", "123")
        try:
            raise ValueError("known failure")
        except ValueError:
            standard.exception("ASR failed")
    finally:
        standard.removeHandler(bridge)
        standard.setLevel(original)
        logger.remove(sink)
    assert [record["message"] for record in records] == ["ASR start 123", "ASR failed"]
    assert records[0]["name"] == "test_douyin_service_bridge"
    assert records[1]["exception"].type is ValueError


@pytest.mark.parametrize(("language", "expected"), [
    ("en", "ChromaDB initialized: local/收藏 (douyin=3)"),
    ("zh", "ChromaDB 初始化完成: local/收藏 (douyin=3)"),
    ("fr", "ChromaDB initialized: local/收藏 (douyin=3)"),
])
def test_console_language_preserves_values_and_original_file_logs(language, expected):
    console = StringIO()
    archive = StringIO()
    console_sink = logger.add(console, format=_console_format, colorize=False, diagnose=False)
    archive_sink = logger.add(archive, format="{message}", diagnose=False)
    standard = logging.Logger("test_console_language", level=logging.INFO)
    standard.addHandler(_ApplicationLogHandler(language))
    try:
        standard.info("ChromaDB 初始化完成: %s (%s)", "local/收藏", "douyin=3")
        try:
            raise ValueError("underlying failure")
        except ValueError:
            standard.exception("问答失败")
    finally:
        logger.remove(console_sink)
        logger.remove(archive_sink)

    assert expected in console.getvalue()
    assert "ValueError: underlying failure" in console.getvalue()
    assert "ChromaDB 初始化完成: local/收藏 (douyin=3)" in archive.getvalue()
    if language != "zh":
        assert "Failed to answer the question" in console.getvalue()
