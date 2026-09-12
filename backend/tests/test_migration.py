"""
数据库迁移与外键完整性单测
"""
import sqlite3
from pathlib import Path

import pytest

from app.db.migration import migrate, preflight, verify


def test_migrator_preflight_and_idempotence(tmp_path: Path):
    """验证迁移的初次执行、外键检测及幂等性"""
    db_file = tmp_path / "test_migration.db"

    conn = sqlite3.connect(str(db_file))

    # 创建旧版本表结构
    conn.executescript("""
        CREATE TABLE favorite_collections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform_collection_id TEXT NOT NULL,
            title TEXT NOT NULL,
            video_count INTEGER DEFAULT 0,
            cover_url TEXT,
            is_active BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE favorite_videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collection_id INTEGER,
            platform_item_id TEXT NOT NULL,
            title TEXT NOT NULL,
            author TEXT,
            duration INTEGER,
            cover_url TEXT,
            video_url TEXT,
            is_active BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (collection_id) REFERENCES favorite_collections(id)
        );
        CREATE TABLE video_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform_item_id TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            transcript_text TEXT DEFAULT '',
            summary TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            error_message TEXT DEFAULT '',
            processed_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO favorite_collections (id, platform_collection_id, title, video_count)
        VALUES (1, 'col_1', '测试收藏夹', 1);
        INSERT INTO favorite_videos (id, collection_id, platform_item_id, title, duration)
        VALUES (10, 1, 'video_100', '测试作品', 15);
        INSERT INTO video_cache (platform_item_id, title, status)
        VALUES ('video_100', '测试作品', 'done');
    """)
    conn.commit()
    conn.close()

    # 1. 运行迁移
    report = migrate(db_path=db_file, skip_backup=True)
    assert report["status"] == "success"

    # 验证数据迁移正确性
    conn = sqlite3.connect(str(db_file))
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM content_items")
    assert cur.fetchone()[0] == 1
    cur.execute("SELECT count(*) FROM collection_items")
    assert cur.fetchone()[0] == 1
    cur.execute("SELECT count(*) FROM ingestion_items")
    assert cur.fetchone()[0] == 1
    cur.execute("SELECT count(*) FROM content_parts")
    assert cur.fetchone()[0] == 1

    # 验证外键检测
    cur.execute("PRAGMA foreign_key_check")
    assert cur.fetchall() == []
    conn.close()

    # 2. 校验 verification 工具
    ver_report = verify(db_path=db_file)
    assert ver_report["status"] == "verified"
    assert ver_report["fk_check"] == "ok"
    assert ver_report["row_counts"]["content_items"] == 1

    # 3. 幂等性测试：再次迁移应当安全跳过 (status == 'skipped')
    report2 = migrate(db_path=db_file, skip_backup=True)
    assert report2["status"] == "skipped"
