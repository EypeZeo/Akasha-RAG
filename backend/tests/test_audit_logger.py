from __future__ import annotations

import datetime
import json

from loguru import logger

from app.core.audit import AuditEvent, AuditEventType, AuditLogger


def test_audit_sink_preserves_application_logging_and_only_stores_audit_events(tmp_path):
    application_messages = []
    application_handler = logger.add(lambda record: application_messages.append(str(record)), format="{message}")
    audit = AuditLogger(tmp_path)
    try:
        logger.info("ordinary-application-record")
        audit.log_settings_change("127.0.0.1", "model_name", "before", "after")
        logger.complete()
        assert any("ordinary-application-record" in message for message in application_messages)
        lines = (tmp_path / "audit.log").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["details"] == {"old_value": "before", "new_value": "after"}
    finally:
        logger.remove(audit._handler_id)
        logger.remove(application_handler)


def test_audit_settings_and_nested_details_do_not_persist_secret_values(tmp_path):
    audit = AuditLogger(tmp_path)
    try:
        audit.log_settings_change("127.0.0.1", "dashscope_api_key", "old-secret", "new-secret")
        audit.log_settings_change("127.0.0.1", "provider", {"api_key": "nested-old-secret"}, {"api_key": "nested-new-secret"})
        audit.log_event(AuditEvent(
            timestamp=datetime.datetime.now(), event_type=AuditEventType.LOGIN_SUCCESS,
            ip_address="127.0.0.1", action="login", result="success",
            details={"profile": {"nickname": "test", "cookies": [{"value": "cookie-secret"}]},
                     "credentials": {"session": "credential-secret"}, "items": [{"token": "token-secret"}]},
        ))
        logger.complete()
        content = (tmp_path / "audit.log").read_text(encoding="utf-8")
        for secret in ("old-secret", "new-secret", "cookie-secret", "credential-secret", "token-secret", "nested-old-secret", "nested-new-secret"):
            assert secret not in content
        records = [json.loads(line) for line in content.splitlines()]
        assert records[2]["details"]["profile"]["nickname"] == "test"
    finally:
        logger.remove(audit._handler_id)
