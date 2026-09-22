"""Additive SQLite migration and bounded history-export limits."""
from sqlalchemy import Engine

MAX_EXPORT_MESSAGES = 10000
MAX_EXPORT_BYTES = 8 * 1024 * 1024


def ensure_chat_client_key_column(engine: Engine) -> None:
    """Add `client_key` and its per-session unique index to an existing SQLite database."""
    if engine.dialect.name != "sqlite":
        return
    with engine.begin() as connection:
        columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(chat_messages)")}
        if "client_key" not in columns:
            connection.exec_driver_sql("ALTER TABLE chat_messages ADD COLUMN client_key VARCHAR(128)")
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_messages_session_client_key "
            "ON chat_messages (session_id, client_key) WHERE client_key IS NOT NULL"
        )
