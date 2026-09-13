"""
FastAPI 应用入口

启动命令：
    uv run --project backend uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 --app-dir backend
"""
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from sqlalchemy import update

from app._version import get_version
from app.api.router import api_router
from app.core.config import settings
from app.core.logging import setup_logging
from app.db.base import Base
from app.db.session import engine, session_factory
from app.models.entities import VideoCache


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI 应用生命周期管理

    启动时：
    - 初始化日志系统
    - 创建数据库表
    - 创建存储目录

    关闭时：
    - 释放数据库连接
    """
    # === 启动 ===
    setup_logging()
    logger.info("正在启动 Akasha-RAG 后端服务...")

    # 创建存储目录
    storage_dirs = [
        settings.chroma_persist_dir,
        settings.audio_cache_dir,
        settings.playwright_user_data_dir,
        settings.playwright_browsers_path,
        "logs",
    ]
    for dir_path in storage_dirs:
        os.makedirs(dir_path, exist_ok=True)
    logger.info("存储目录初始化完成")

    # A v0.6 database must be migrated before SQLAlchemy creates any v0.7
    # tables.  Creating the new tables first strands legacy data and makes the
    # table-rebuild migration collide with existing indexes.
    db_file = Path(engine.url.database) if engine.url.database else None
    if db_file is not None:
        from app.db.migration import migrate, needs_legacy_migration
        if needs_legacy_migration(db_file):
            logger.warning("检测到旧版数据库，正在执行 v0.7.0 升级迁移")
            report = migrate(db_path=db_file)
            if report.get("status") not in {"success", "skipped"}:
                raise RuntimeError(f"数据库升级失败: {report}")
            logger.info("数据库升级完成: %s", report)

    # 创建数据库表（同步引擎；新安装或已迁移数据库）
    Base.metadata.create_all(bind=engine)
    from app.services.account_state import ensure_source_account_profile_columns
    ensure_source_account_profile_columns(engine)
    from app.services.favorites_service import ensure_content_item_enrichment_column
    ensure_content_item_enrichment_column(engine)
    logger.info("数据库表初始化完成")

    # Eagerly initialize the platform-partitioned Chroma collections so a
    # broken vector store fails fast at startup instead of on first request.
    from app.services.chroma_service import get_chroma_service
    get_chroma_service()

    # This launcher uses one backend process. Only recover interrupted states
    # before its worker starts, preserving any durable transcript checkpoint.
    with session_factory() as db:
        recovered = db.execute(
            update(VideoCache)
            .where(VideoCache.status.in_(("downloading", "transcribing")))
            .values(status="pending", error_message="上次进程中断，可继续入库")
        ).rowcount
        db.commit()
    if recovered:
        logger.warning("恢复 {} 个中断的入库项为待入库，已保留正文检查点", recovered)

    # 启动后台 worker
    from app.services.worker import worker
    worker.start()
    logger.info("后台工作线程已启动")

    # 启动音频缓存自动定时清理调度器（启动即清理 + 后台周期自动轮询）
    from app.services.media_service import start_cache_cleaner_scheduler, stop_cache_cleaner_scheduler
    start_cache_cleaner_scheduler()

    yield

    # === 关闭 ===
    logger.info("正在关闭 Akasha-RAG 后端服务...")
    worker.stop()
    stop_cache_cleaner_scheduler()
    from app.services import export_worker
    export_worker.shutdown()
    from app.services.bilibili.client import bilibili_client
    await bilibili_client.aclose()
    engine.dispose()
    logger.info("数据库连接已释放")


# 创建 FastAPI 应用
app = FastAPI(
    title="Akasha-RAG",
    description="Akasha-RAG · 多平台收藏夹 RAG 知识库 API",
    version=get_version(),
    lifespan=lifespan,
)

# CORS 中间件（允许前端开发服务器跨域）
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(api_router)


@app.get("/")
async def root():
    """
    根路径健康检查

    :return: 服务状态信息
    """
    return {
        "status": "ok",
        "service": "Akasha-RAG",
        "version": app.version,
    }
