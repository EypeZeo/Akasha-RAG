"""
知识库路由模块

提供收藏夹一键入库、入库进度查询、知识库统计、视频内容导出等接口。
"""
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.db.session import get_db
from app.models.entities import CollectionItemRelation, ContentItem, VideoCache
from app.services.batch_export_service import batch_export_service
from app.services.collection_scope import AmbiguousCollectionError, CollectionNotFoundError, resolve_collection
from app.services.knowledge_service import knowledge_service
from app.services.markdown_export import export_ai_organized, export_original
from app.services.worker import worker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge", tags=["知识库"])


# Every identifier that reaches a lookup is bounded, not just the list that holds it.
RemoteId = Annotated[str, StringConstraints(max_length=64)]


class SyncRequest(BaseModel):
    scope: Literal["all", "selected"] = "all"
    collection_id: str | None = Field(default=None, max_length=64)
    content_type: Literal["all", "video", "note"] = "all"
    selected_ids: list[RemoteId] = Field(default_factory=list, max_length=10000)
    platform: Literal["all", "douyin", "bilibili"] = "all"

@router.post("/sync")
async def sync_knowledge(body: SyncRequest, db: Session = Depends(get_db)):
    """
    触发知识库入库任务

    支持全量/分类范围模式（scope='all'，数据库原子提取，零网络传输巨量ID）
    以及精准指定模式（scope='selected'，传输显式勾选的 selected_ids）。

    :param body: 请求体，包含 scope, collection_id, content_type, selected_ids, platform
    :param db: 数据库会话
    :return: 任务 ID 和待处理数量
    """
    try:
        result = knowledge_service.start_sync(
            db, scope=body.scope, collection_id=body.collection_id,
            content_type=body.content_type, selected_ids=body.selected_ids or None,
            platform=body.platform,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, **result}


@router.get("/sync/{task_id}")
async def get_sync_progress(task_id: str):
    """
    查询入库任务进度

    :param task_id: 任务 ID（由 /sync 返回）
    :return: 任务状态和进度
    """
    progress = knowledge_service.get_progress(task_id)
    if progress is None:
        return {
            "success": False,
            "message": f"任务不存在: {task_id}",
        }
    return {"success": True, **progress}


@router.post("/sync/{task_id}/cancel")
async def cancel_sync(task_id: str):
    """
    取消入库任务

    正在下载/转写中的 1-3 项会结束当前步骤后停止，残留状态回退为待入库。
    """
    ok = knowledge_service.cancel_sync(task_id)
    return {
        "success": ok,
        "message": "已请求取消" if ok else "任务不存在或已结束",
    }


@router.get("/pending")
async def list_pending_items(
    collection_id: str | None = Query(None, max_length=64),
    content_type: str = Query("all", pattern="^(all|video|note)$"),
    platform: Literal["all", "douyin", "bilibili"] | None = Query(None, description="平台过滤: douyin | bilibili | all"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """
    分批获取待入库的内容列表（轻量按需分页 + 全局各分类计数分离）

    :param collection_id: 可选，限定特定收藏夹
    :param content_type: 可选，all | video | note
    :param platform: 可选平台过滤
    :param page: 页码（从 1 开始）
    :param page_size: 每页大小（默认 50，上限 100）
    :param db: 数据库会话
    :return: 包含 items, total, video_count, note_count, page, page_size, has_more
    """
    try:
        data = knowledge_service.list_pending_items(
            db, collection_id=collection_id, content_type=content_type,
            page=page, page_size=page_size, platform=platform,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, **data}


@router.post("/reset-failed")
async def reset_failed_videos(db: Session = Depends(get_db)):
    """
    将所有失败/卡住的视频重置为 pending

    处理场景：
    - 入库过程被中断（刷新页面、服务重启）
    - 视频转写失败需要重试

    :param db: 数据库会话
    :return: 重置数量
    """
    from sqlalchemy import update as sql_update
    if worker.has_active_tasks():
        return {
            "success": False,
            "reset_count": 0,
            "message": "入库任务仍在执行或排队，请等待任务结束后重置失败项",
        }
    reset_statuses = ["failed", "downloading", "transcribing"]
    result = db.execute(
        sql_update(VideoCache)
        .where(VideoCache.status.in_(reset_statuses))
        .values(status="pending", error_message="")
    )
    db.commit()
    count = result.rowcount
    return {"success": True, "reset_count": count}


@router.delete("/videos/{platform_item_id}")
async def delete_video(platform_item_id: str, platform: str | None = Query(None), db: Session = Depends(get_db)):
    """
    删除已入库视频

    清理 ChromaDB 向量、重置为 pending 状态、删除音频缓存。
    不允许删除正在入库中的视频。

    :param platform_item_id: 抖音视频 ID
    :param db: 数据库会话
    :return: 删除结果
    """
    try:
        result = knowledge_service.delete_video(db, platform_item_id, platform)
        return {"success": True, **result}
    except ValueError as exc:
        return {"success": False, "message": str(exc)}


@router.get("/stats")
async def get_knowledge_stats(db: Session = Depends(get_db)):
    """
    获取知识库统计信息

    :param db: 数据库会话
    :return: VideoCache 各状态数量 + ChromaDB 存储统计
    """
    stats = knowledge_service.get_stats(db)
    return {"success": True, **stats}


@router.get("/export/{platform_item_id}", response_class=PlainTextResponse)
async def export_video_markdown(
    platform_item_id: str,
    mode: str = Query("original", pattern="^(original|ai)$"),
    platform: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """
    导出视频内容为 Markdown

    两种模式：
    - original：原始 ASR 转写全文 + 元信息
    - ai：AI 结构化整理（摘要/观点/提纲/建议）+ 原始转写

    :param platform_item_id: 抖音视频 ID
    :param mode: 导出模式（original / ai）
    :param db: 数据库会话
    :return: Markdown 文本（Content-Type: text/plain; charset=utf-8）
    """
    cache_query = select(VideoCache).join(VideoCache.content_item).where(ContentItem.remote_item_id == platform_item_id)
    if platform and platform != "all":
        cache_query = cache_query.where(ContentItem.platform == platform)
    cache = db.execute(cache_query).scalar_one_or_none()

    if cache is None:
        return f"# 导出失败\n\n视频 `{platform_item_id}` 未找到，请先同步收藏夹。\n"

    try:
        if mode == "original":
            md = export_original(cache)
        else:
            md = export_ai_organized(cache, db=db)
    except ValueError as exc:
        return f"# 导出失败\n\n{exc}\n"

    from fastapi.responses import Response
    return Response(
        content=md,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename={platform_item_id}.md"
        },
    )


class BatchExportRequest(BaseModel):
    collection_id: str | None = Field(default=None, max_length=64)
    platform: Literal["all", "douyin", "bilibili"] = "all"
    selected_ids: list[RemoteId] | None = Field(default=None, max_length=10000)
    content_type: Literal["original", "ai", "both"] = "both"
    format: Literal["markdown", "word", "excel", "ppt", "pdf"] = "markdown"
    pack_mode: Literal["single", "zip"] = "single"
    target_dir: str | None = Field(default=None, max_length=4096)
    auto_open: bool = True


@router.post("/export/batch")
async def export_batch_knowledge(body: BatchExportRequest, db: Session = Depends(get_db)):
    """
    提交批量导出为后台任务，立即返回 task_id。

    支持 Markdown / Word / Excel / PPT / PDF，单文件合并或多文件独立。
    指定 target_dir → 直接写入本地目录（可自动打开）；否则产物暂存供浏览器下载。
    导出（尤其含 AI 整理时逐条调 LLM）耗时较长，进度用 GET /export/batch/{task_id} 轮询。

    收藏夹或 selected_ids 无法唯一确定时在**入队之前**就拒绝（400/404），
    不会先返回一个"成功"的任务、再让它在后台失败。
    """
    from app.services import export_worker

    try:
        await run_in_threadpool(
            batch_export_service.validate_scope, db, body.collection_id, body.selected_ids, body.platform,
        )
    except AmbiguousCollectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CollectionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    task_id = export_worker.submit_export({
        "collection_id": body.collection_id,
        "platform": body.platform,
        "selected_ids": body.selected_ids,
        "content_type": body.content_type,
        "format": body.format,
        "pack_mode": body.pack_mode,
        "target_dir": body.target_dir,
        "auto_open": body.auto_open,
    })
    mode = "local" if (body.target_dir or "").strip() else "browser"
    return {"success": True, "task_id": task_id, "mode": mode}


@router.get("/export/batch/{task_id}")
async def get_export_progress(task_id: str):
    """查询批量导出任务进度。"""
    from app.services import export_worker

    progress = export_worker.get_progress(task_id)
    if progress is None:
        return {"success": False, "message": "任务不存在或已过期"}
    return {"success": True, **progress}


@router.get("/export/batch/{task_id}/download")
async def download_export_artifact(task_id: str):
    """浏览器模式：任务完成后下载产物文件（产物保留一段时间，可反复下载）。"""
    from fastapi.responses import Response
    from app.services import export_worker

    taken = export_worker.take_download(task_id)
    if taken is None:
        return Response("导出产物不存在或已过期", status_code=404)
    path, download_name, mime = taken
    return FileResponse(
        path,
        media_type=mime,
        filename=download_name,
        headers={"Access-Control-Expose-Headers": "Content-Disposition"},
    )


class ClearAllRequest(BaseModel):
    collection_id: str | None = Field(default=None, max_length=64)
    platform: Literal["all", "douyin", "bilibili"] | None = None


@router.post("/clear-all")
async def clear_all_knowledge(
    body: ClearAllRequest = ClearAllRequest(),
    db: Session = Depends(get_db),
):
    """
    一键清空入库数据

    清理 ChromaDB 向量库、重置所有/指定收藏夹 VideoCache 为 pending 状态，
    清空转写文本及缓存音频文件。
    """
    if worker.has_active_tasks():
        # 入库流水线用一次比较并置换的 SQL 认领条目后，从不再检查这条记录
        # 是否被清空操作重置过——如果这里在有活跃任务时继续执行，worker
        # 线程稍后仍会把向量写进刚清空重建的 Chroma 集合，而 DB 那边已经
        # 被这次清空重置成 pending，两边状态不一致。和 reset_failed_videos
        # 用的是同一条防线。
        return {
            "success": False,
            "message": "入库任务仍在执行或排队，请等待任务结束后清空",
            "chroma_cleared": False,
        }
    return await run_in_threadpool(_clear_all_knowledge_sync, db, body)


def _clear_all_knowledge_sync(db: Session, body: ClearAllRequest) -> dict:
    from sqlalchemy import update as sql_update
    from app.services.chroma_service import get_chroma_service

    chroma_cleared = False
    try:
        chroma = get_chroma_service()
        if body.collection_id and body.collection_id != "all":
            # 仅清空特定收藏夹
            collection = resolve_collection(db, body.collection_id, body.platform)
            collection_pk = collection.id if collection else None
            if collection_pk is None:
                return {"success": False, "message": "指定收藏夹不存在或平台不匹配"}
            rows = db.execute(
                select(ContentItem.id, ContentItem.remote_item_id, ContentItem.platform).join(
                    CollectionItemRelation, CollectionItemRelation.content_item_id == ContentItem.id
                ).where(CollectionItemRelation.collection_id == collection_pk, CollectionItemRelation.is_active.is_(True))
            ).all()
            vids = [row.remote_item_id for row in rows]
            if vids:
                for row in rows:
                    chroma.delete_by_video(row.remote_item_id, platform=row.platform, content_item_id=row.id)
                db.execute(
                    sql_update(VideoCache)
                    .where(VideoCache.content_item_id.in_([row.id for row in rows]))
                    .values(status="pending", transcript_text="", summary="", error_message="")
                )
                db.commit()
            return {"success": True, "reset_count": len(vids)}
        else:
            # 清空全部，或按平台清空一个平台
            #
            # Chroma 与 SQLite 是两个独立存储，没有共同事务；顺序必须是先清
            # 向量库、再改数据库行，且两步都设计成幂等/可重试：
            # - 先清 Chroma：万一后面 SQL 那步失败，重试时重新执行"删了再建"
            #   的空集合操作不会报错，行为和第一次调用完全一样。
            # - 后清 SQL：如果反过来先改数据库状态、Chroma 清空失败，用户会
            #   看到"已重置为待入库"，但向量库里还留着旧内容——下次重建会在
            #   残留向量之上叠加，比"提示清空没做完、可以重试"更糟。
            target_platform = body.platform if body.platform and body.platform != "all" else None
            if target_platform:
                chroma.clear_platform(target_platform)
            else:
                chroma.clear_all()
            chroma_cleared = True

            update_stmt = sql_update(VideoCache).values(
                status="pending", transcript_text="", summary="", error_message=""
            )
            if target_platform:
                scoped_content_ids = select(ContentItem.id).where(ContentItem.platform == target_platform)
                update_stmt = update_stmt.where(VideoCache.content_item_id.in_(scoped_content_ids))
            result = db.execute(update_stmt)
            db.commit()
            return {"success": True, "reset_count": result.rowcount}

    except AmbiguousCollectionError as exc:
        # A bad request, not a server fault: same 400 as every other endpoint, no error log.
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("清空入库数据失败")
        db.rollback()
        return {
            "success": False,
            "message": str(exc),
            # 告诉调用方向量库那一步是否已经成功——重试是安全的，
            # 不会因为"已经清过一次"而报错或产生副作用。
            "chroma_cleared": chroma_cleared,
        }
