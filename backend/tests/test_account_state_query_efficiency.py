"""
PERF-07 回归测试：record_account_state 重复写入同样的值不应发出 UPDATE

`status`/`auth_state_ref` 已经有"值不同才赋值"的判断，但 `nickname`/
`avatar_url` 在 `active=True` 时只要非空就无条件重新赋值——实测（写一个
真实的 SQLAlchemy event 计数脚本）证明重复赋相同值确实会触发一次真实
UPDATE，这是 `/api/auth/platforms` 30s 轮询场景下的真实重复写库，不是
理论问题。
"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.services.account_state import record_account_state


def _count_updates(engine, fn):
    log = []
    listener = lambda *a: log.append(a[2])
    event.listen(engine, "before_cursor_execute", listener)
    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    return [q for q in log if q.strip().upper().startswith("UPDATE")]


def test_repeated_identical_poll_does_not_issue_a_second_update():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)

    with factory() as db:
        record_account_state(
            db, "douyin", active=True, auth_state_ref="state.json",
            nickname="张三", avatar_url="https://p3-sign.douyinpic.com/a.jpg",
        )
        db.commit()

        updates = _count_updates(engine, lambda: (
            record_account_state(
                db, "douyin", active=True, auth_state_ref="state.json",
                nickname="张三", avatar_url="https://p3-sign.douyinpic.com/a.jpg",
            ),
            db.commit(),
        ))
        assert updates == []


def test_a_genuine_change_still_issues_an_update():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)

    with factory() as db:
        record_account_state(
            db, "douyin", active=True, auth_state_ref="state.json",
            nickname="张三", avatar_url="https://p3-sign.douyinpic.com/a.jpg",
        )
        db.commit()

        updates = _count_updates(engine, lambda: (
            record_account_state(
                db, "douyin", active=True, auth_state_ref="state.json",
                nickname="李四",  # genuinely different nickname
                avatar_url="https://p3-sign.douyinpic.com/a.jpg",
            ),
            db.commit(),
        ))
        assert len(updates) == 1
