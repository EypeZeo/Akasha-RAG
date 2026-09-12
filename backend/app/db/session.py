"""
数据库会话管理模块

提供 SQLAlchemy Engine 和 Session 工厂。
使用同步引擎（SQLite 本地项目），与三个参考项目保持一致。
"""
from pathlib import Path
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


def _resolve_sqlite_url(raw_url: str) -> str:
    """自动将 SQLite 相对路径解析为绝对路径，避免在不同工作目录下启动找不到数据库文件"""
    if not raw_url.startswith("sqlite:///"):
        return raw_url
    rel_path = raw_url[len("sqlite:///"):]
    p = Path(rel_path)
    if p.is_absolute():
        return raw_url
    candidates = [
        Path.cwd() / p,
        Path(__file__).resolve().parent.parent / "storage" / p.name,
        Path.cwd() / "backend" / "app" / "storage" / p.name,
    ]
    for cand in candidates:
        if cand.exists():
            return f"sqlite:///{cand.as_posix()}"
    target = candidates[1]
    target.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{target.as_posix()}"


resolved_db_url = _resolve_sqlite_url(settings.database_url)

# 连接参数优化（针对 SQLite 避免多线程锁定）
connect_args = {}
if resolved_db_url.startswith("sqlite"):
    connect_args = {
        "check_same_thread": False,
        "timeout": 30,
    }

# 同步引擎
engine = create_engine(
    resolved_db_url,
    connect_args=connect_args,
    echo=False,
    future=True,
)

# 开启 SQLite WAL 模式与繁忙超时，大幅提升多线程并发读写能力并防止死锁
if resolved_db_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        try:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL;")
            # SQLite does not enforce declared foreign keys unless this is set
            # for every connection.  The normalized v0.7 schema relies on
            # cascade semantics for collection/content/task cleanup.
            cursor.execute("PRAGMA foreign_keys=ON;")
            cursor.execute("PRAGMA synchronous=NORMAL;")
            cursor.execute("PRAGMA busy_timeout=30000;")
            cursor.close()
        except Exception:
            pass

# 会话工厂
session_factory = sessionmaker(
    engine,
    class_=Session,
    expire_on_commit=False,
)


def get_db() -> Session:
    """
    获取数据库会话（FastAPI 依赖注入）

    用法：
        @app.get("/path")
        async def handler(db: Session = Depends(get_db)):
            ...

    :return: SQLAlchemy Session 对象
    """
    session = session_factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    else:
        # Only commit if no exception occurred during request processing.
        # FastAPI calls finally block even after exceptions, which previously
        # caused partial writes to leak through after validation failures.
        session.commit()
    finally:
        session.close()
