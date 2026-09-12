import asyncio
import sqlite3
import threading
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from sqlalchemy import create_engine

from app.services.account_state import ensure_source_account_profile_columns
from app.services.bilibili.client import BilibiliClient
from app.services.douyin_collector import DouyinCollector


def test_account_profile_columns_upgrade_an_existing_sqlite_database(tmp_path):
    database = tmp_path / "legacy.db"
    conn = sqlite3.connect(database)
    conn.execute("""CREATE TABLE source_accounts (
        id INTEGER PRIMARY KEY, platform TEXT, local_profile_id TEXT,
        auth_state_ref TEXT, auth_expiry DATETIME, status TEXT,
        created_at DATETIME, updated_at DATETIME
    )""")
    conn.commit()
    conn.close()

    engine = create_engine(f"sqlite:///{database}")
    ensure_source_account_profile_columns(engine)
    ensure_source_account_profile_columns(engine)
    with engine.connect() as connection:
        columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(source_accounts)")}
    assert {"nickname", "avatar_url"}.issubset(columns)


def test_bilibili_cached_status_never_calls_remote_nav():
    client = object.__new__(BilibiliClient)
    client._cookies = {"SESSDATA": "session", "DedeUserID": "42"}
    client._user_info = {"mid": "42", "uname": "缓存昵称", "face": "https://example.test/avatar.jpg"}
    client.get_user_info = Mock()

    status = asyncio.run(BilibiliClient.get_auth_status(client))

    assert status.is_logged_in is True
    assert status.nickname == "缓存昵称"
    client.get_user_info.assert_not_called()


def test_douyin_logout_does_not_close_a_context_from_the_request_thread(tmp_path):
    collector = DouyinCollector()
    collector.user_data_dir = Path(tmp_path / "profile")
    collector.user_data_dir.mkdir()
    collector.storage_state_path = collector.user_data_dir / "state.json"
    collector.storage_state_path.write_text('{"cookies": []}', encoding="utf-8")
    context = Mock()
    collector._active_context = context
    collector._login_task = None
    collector._logout_requested = threading.Event()

    ok, _ = collector.logout()

    assert ok is True
    context.close.assert_not_called()
    assert collector._logout_requested.is_set()


def test_douyin_blocks_a_new_login_until_a_locked_profile_is_reclaimed():
    collector = DouyinCollector()
    collector._profile_cleanup_pending.set()

    ok, message = collector.start_login()

    assert ok is False
    assert "安全清理" in message


def test_douyin_retiring_login_worker_does_not_overwrite_logout_status():
    collector = DouyinCollector()
    collector.status = "idle"
    collector._logout_requested.set()
    broken_playwright = MagicMock()
    broken_playwright.return_value.__enter__.side_effect = RuntimeError("browser stopped")

    with patch("app.services.douyin_collector.sync_playwright", broken_playwright):
        collector._login_and_fetch_sync()

    assert collector.status == "idle"
