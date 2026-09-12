"""
BUG-18 回归测试：SQLite 回滚必须和备份一样对 WAL 安全

`backup()` 一直都在正确使用 SQLite Backup API；`rollback()` 之前是裸文件
`shutil.copy2()`，完全不处理目标库自己可能存在的 WAL——这不是"清理一下
sidecar 文件"的小问题，而是会丢数据（WAL 里可能有已提交但未 checkpoint
进主文件的行）。修复后 `rollback()` 复用和 `backup()` 对称的 Backup API。
"""
import sqlite3

import pytest

from app.db.migration import backup, rollback


def _make_db_with_uncheckpointed_wal(db_path, value: str):
    """写入一行数据、只 commit 不 checkpoint，让它只存在于 WAL 里。"""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS t (v TEXT)")
    conn.execute("INSERT INTO t (v) VALUES (?)", (value,))
    conn.commit()
    return conn  # 调用方负责关闭，关闭前 WAL 里的数据不会被自动 checkpoint 进主文件


def _read_values(db_path) -> list[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return [row[0] for row in conn.execute("SELECT v FROM t ORDER BY v")]
    finally:
        conn.close()


def test_backup_captures_uncheckpointed_wal_data(tmp_path):
    """基线：backup() 本来就是对的，先确认这一点没有被本次改动破坏。"""
    db_file = tmp_path / "douyinrag.db"
    conn = _make_db_with_uncheckpointed_wal(db_file, "only-in-wal")
    try:
        result = backup(db_path=db_file)
    finally:
        conn.close()

    assert _read_values(result["db_backup"]) == ["only-in-wal"]


def test_rollback_restores_backup_without_losing_or_replaying_stale_data(tmp_path):
    """核心场景：备份时的数据只在 WAL 里；之后数据库被改坏；rollback() 必须
    精确恢复到备份时刻的内容，且不能让目标库自己的旧 WAL 在下次打开时把
    "改坏"之后的状态又重放回来。"""
    db_file = tmp_path / "douyinrag.db"
    conn = _make_db_with_uncheckpointed_wal(db_file, "good-state")
    try:
        backup_result = backup(db_path=db_file)
    finally:
        conn.close()

    # 模拟"迁移出错"：数据库继续被写坏，且同样只提交到 WAL、不 checkpoint。
    bad_conn = _make_db_with_uncheckpointed_wal(db_file, "bad-state")
    bad_conn.close()
    assert sorted(_read_values(db_file)) == ["bad-state", "good-state"]

    rollback(db_path=db_file, backup_file=backup_result["db_backup"])

    assert _read_values(db_file) == ["good-state"]

    # 还原后不应残留一个非空的 -wal——否则下一次连接打开这个文件时，
    # SQLite 会自动重放里面的帧，可能把已经回滚掉的状态又带回来。
    wal_file = db_file.parent / (db_file.name + "-wal")
    if wal_file.exists():
        assert wal_file.stat().st_size == 0


def test_rollback_refuses_when_target_db_is_busy(tmp_path):
    """目标库仍被另一个连接（模拟仍在运行的后端）占用时，rollback() 必须
    明确报错，而不是在服务还在写入的情况下静默产生一个损坏/撕裂的结果。"""
    db_file = tmp_path / "douyinrag.db"
    conn = _make_db_with_uncheckpointed_wal(db_file, "good-state")
    try:
        backup_result = backup(db_path=db_file)
    finally:
        conn.close()

    holder = sqlite3.connect(str(db_file))
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("INSERT INTO t (v) VALUES ('in-flight')")
    try:
        with pytest.raises(RuntimeError, match="仍被其它连接占用|running"):
            rollback(db_path=db_file, backup_file=backup_result["db_backup"])
    finally:
        holder.rollback()
        holder.close()
