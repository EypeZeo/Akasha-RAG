"""
BUG-10/NET-04 迁移回归测试：`last_enriched_at` 列的幂等补列

和 `account_state.py::ensure_source_account_profile_columns` 同款模式——
旧库没有这一列，跑一次 `ensure_content_item_enrichment_column` 之后
`PRAGMA table_info` 必须能看到它，且可以正常读写；新建库（含这一列）
再跑一次也不能报错（幂等）。
"""
import sqlite3

from sqlalchemy import create_engine

from app.db.base import Base
from app.services.favorites_service import ensure_content_item_enrichment_column


def _table_columns(db_path: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[1] for row in conn.execute("PRAGMA table_info(content_items)")}
    finally:
        conn.close()


def test_adds_the_column_to_an_old_schema_missing_it(tmp_path):
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE content_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL DEFAULT 'douyin',
            remote_item_id TEXT NOT NULL
        );
        INSERT INTO content_items (platform, remote_item_id) VALUES ('bilibili', 'BV1xx');
        """
    )
    conn.commit()
    conn.close()

    assert "last_enriched_at" not in _table_columns(str(db_path))

    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    ensure_content_item_enrichment_column(engine)

    assert "last_enriched_at" in _table_columns(str(db_path))

    # Can read/write the new column now.
    conn = sqlite3.connect(str(db_path))
    conn.execute("UPDATE content_items SET last_enriched_at = '2026-01-01 00:00:00'")
    conn.commit()
    value = conn.execute("SELECT last_enriched_at FROM content_items").fetchone()[0]
    conn.close()
    assert value == "2026-01-01 00:00:00"


def test_is_idempotent_on_a_freshly_created_schema(tmp_path):
    db_path = tmp_path / "new.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    Base.metadata.create_all(engine)

    assert "last_enriched_at" in _table_columns(str(db_path))

    # Running it again on a schema that already has the column must not raise.
    ensure_content_item_enrichment_column(engine)
    ensure_content_item_enrichment_column(engine)

    assert "last_enriched_at" in _table_columns(str(db_path))
