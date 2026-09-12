"""Services must reach rotating logs even when console logging is disabled."""
import logging

from loguru import logger

from app.core.logging import _ApplicationLogHandler


def test_stdlib_service_messages_and_exceptions_reach_loguru():
    records = []
    sink = logger.add(lambda message: records.append(message.record), diagnose=False)
    standard = logging.getLogger("test_douyin_service_bridge")
    bridge = _ApplicationLogHandler()
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
