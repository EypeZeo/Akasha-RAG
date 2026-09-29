"""
收藏夹路由模块

提供收藏夹同步、列表查询、视频列表等接口。
"""
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.collection_scope import AmbiguousCollectionError
from app.services.favorites_service import favorites_service
from app.services.platform_registry import PlatformFilter, supported_platforms

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/favorites", tags=["收藏夹"])


@router.post("/sync")
async def sync_favorites(
    platform: str = Query(
        "douyin",
        description=f"同步平台: {' | '.join((*supported_platforms(), 'all'))}",
    ),
    db: Session = Depends(get_db),
):
    """
    同步收藏夹数据 (支持抖音、B 站与知乎)

    拉取最新收藏夹和内容列表，与本地数据库差异对齐。
    新增的内容会自动创建 IngestionItem pending 记录，供知识库入库使用。

    :param platform: 目标平台 (douyin | bilibili | all)
    :param db: 数据库会话
    :return: 同步结果统计
    """
    if platform != "all" and platform not in supported_platforms():
        return {"success": False, "message": "不支持的平台"}
    from app.services.worker import worker
    if platform != "all" and worker.is_platform_blocked(platform):
        return {"success": False, "message": "该平台已退出登录，请重新登录后再同步"}
    try:
        if platform == "bilibili":
            result = await favorites_service.sync_from_bilibili(db)
            total = result.get("videos_total", 0)
            invalid_cnt = result.get("invalid_count", 0)
            if invalid_cnt > 0:
                summary_msg = f"已同步 {total} 个视频，其中来自哔哩哔哩平台 {total} 个视频，已失效视频 {invalid_cnt} 个无法同步，同步已完成。"
            else:
                summary_msg = f"已同步 {total} 个视频，其中来自哔哩哔哩平台 {total} 个视频，同步已完成。"
            result["summary_message"] = summary_msg
            return {"success": True, **result}
        elif platform == "zhihu":
            result = await favorites_service.sync_from_zhihu(db)
            total = result.get("videos_total", 0)
            article_count = result.get("notes_count", 0)
            invalid_count = result.get("invalid_count", 0)
            summary_msg = f"已同步 {total} 个内容，其中来自知乎平台 {article_count} 个文章/回答"
            if invalid_count > 0:
                summary_msg += f"，已跳过 {invalid_count} 个无效条目"
            result["summary_message"] = summary_msg + "，同步已完成。"
            return {"success": True, **result}
        elif platform == "douyin":
            result = await favorites_service.sync_from_douyin(db)
            total = result.get("videos_total", 0)
            v_cnt = result.get("videos_count", 0)
            n_cnt = result.get("notes_count", 0)
            invalid_cnt = result.get("invalid_count", 0)
            if invalid_cnt > 0:
                summary_msg = f"已同步 {total} 个内容，其中来自抖音平台 {v_cnt} 个视频内容、{n_cnt} 个图文内容，已失效视频 {invalid_cnt} 个无法同步，同步已完成。"
            else:
                summary_msg = f"已同步 {total} 个内容，其中来自抖音平台 {v_cnt} 个视频内容、{n_cnt} 个图文内容，同步已完成。"
            result["summary_message"] = summary_msg
            return {"success": True, **result}
        else:
            results = []
            r1, r2, r3 = None, None, None
            platform_results: dict[str, dict] = {}
            try:
                r1 = await favorites_service.sync_from_douyin(db)
                results.append(r1)
                platform_results["douyin"] = {"success": True}
            except Exception as e:
                # 两个平台共用同一个 db session/事务。save_snapshot_to_db()
                # 可能已经 flush() 了部分尚未提交的写入才抛异常；这里必须
                # 立刻 rollback，否则这些半成品会挂在事务里，被下面 B 站
                # 同步成功后自己的 db.commit() 一并提交上去。
                db.rollback()
                logger.warning("全部同步时抖音失败: %s", e)
                platform_results["douyin"] = {"success": False, "message": str(e)}
            try:
                r2 = await favorites_service.sync_from_bilibili(db)
                results.append(r2)
                platform_results["bilibili"] = {"success": True}
            except Exception as e:
                db.rollback()
                logger.warning("全部同步时B站失败: %s", e)
                platform_results["bilibili"] = {"success": False, "message": str(e)}
            try:
                r3 = await favorites_service.sync_from_zhihu(db)
                results.append(r3)
                platform_results["zhihu"] = {"success": True}
            except Exception as e:
                db.rollback()
                logger.warning("全部同步时知乎失败: %s", e)
                platform_results["zhihu"] = {"success": False, "message": str(e)}

            parts = []
            total_synced = 0
            total_invalid = 0
            added_videos = 0
            removed_videos = 0
            added_notes = 0
            removed_notes = 0

            if r1:
                dy_total = r1.get("videos_total", 0)
                dy_v = r1.get("videos_count", 0)
                dy_n = r1.get("notes_count", 0)
                dy_inv = r1.get("invalid_count", 0)
                total_synced += dy_total
                total_invalid += dy_inv
                added_videos += r1.get("added_videos", 0)
                removed_videos += r1.get("removed_videos", 0)
                added_notes += r1.get("added_notes", 0)
                removed_notes += r1.get("removed_notes", 0)
                parts.append(f"来自抖音平台 {dy_v} 个视频内容、{dy_n} 个图文内容")

            if r2:
                bili_total = r2.get("videos_total", 0)
                bili_inv = r2.get("invalid_count", 0)
                total_synced += bili_total
                total_invalid += bili_inv
                added_videos += r2.get("added_videos", 0)
                removed_videos += r2.get("removed_videos", 0)
                parts.append(f"来自哔哩哔哩平台 {bili_total} 个视频")

            if r3:
                zhihu_total = r3.get("videos_total", 0)
                zhihu_articles = r3.get("notes_count", 0)
                zhihu_invalid = r3.get("invalid_count", 0)
                total_synced += zhihu_total
                total_invalid += zhihu_invalid
                added_videos += r3.get("added_videos", 0)
                removed_videos += r3.get("removed_videos", 0)
                added_notes += r3.get("added_notes", 0)
                removed_notes += r3.get("removed_notes", 0)
                parts.append(f"来自知乎平台 {zhihu_articles} 个文章/回答")

            prefix = f"已同步 {total_synced} 个内容"
            mid = ("，其中" + "，".join(parts)) if parts else ""
            inv_str = f"，已失效视频 {total_invalid} 个无法同步" if total_invalid > 0 else ""
            summary_msg = f"{prefix}{mid}{inv_str}，同步已完成。"

            any_succeeded = any(v["success"] for v in platform_results.values())
            partial = not all(v["success"] for v in platform_results.values())

            return {
                "success": any_succeeded,
                "partial": partial,
                "platform_results": platform_results,
                "results": results,
                "videos_total": total_synced,
                "invalid_count": total_invalid,
                "added_videos": added_videos,
                "removed_videos": removed_videos,
                "added_notes": added_notes,
                "removed_notes": removed_notes,
                "summary_message": summary_msg,
            }
    except Exception as exc:
        import traceback
        import httpx
        # DATA-01: sync_from_douyin/sync_from_bilibili may have already
        # flush()ed partial collection/content/relation changes into this
        # session before failing. Because this except swallows the error and
        # returns a normal response, get_db never sees an exception and would
        # otherwise commit that half-written state on its "no exception" path.
        db.rollback()
        logger.error("收藏夹同步失败: %s\n%s", exc, traceback.format_exc())
        err_type = type(exc).__name__
        err_msg = str(exc).strip()
        if "Timeout" in err_type or isinstance(exc, (httpx.TimeoutException, TimeoutError)):
            msg = "连接平台服务器超时，可能是网络波动或代理延迟，请稍候重试"
        elif "Connect" in err_type or isinstance(exc, (httpx.NetworkError, ConnectionError)):
            msg = "网络连接失败，请检查网络设置或代理"
        elif err_msg:
            msg = f"{err_type}: {err_msg}"
        else:
            msg = f"同步异常 ({err_type})"
        return {"success": False, "message": msg}


@router.get("/collections")
async def list_collections(
    platform: PlatformFilter | None = Query(None, description=f"平台过滤: {' | '.join((*supported_platforms(), 'all'))}"),
    db: Session = Depends(get_db),
):
    """
    获取已同步的收藏夹列表

    第一项固定为"全部收藏"。

    :param platform: 可选平台过滤
    :param db: 数据库会话
    :return: 收藏夹列表
    """
    items = favorites_service.list_collections(db, platform=platform)
    return {
        "success": True,
        "items": items,
        "total": len(items),
    }


@router.get("/collections/{collection_id}/videos")
async def list_collection_videos(
    collection_id: str = Path(max_length=64),
    platform: PlatformFilter | None = Query(None, description=f"平台过滤: {' | '.join((*supported_platforms(), 'all'))}"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=2000),
    cursor: str | None = Query(None, description="基于不可变本地 ID 的不透明游标"),
    status: Literal["all", "pending", "done", "failed", "downloading", "transcribing"] = Query("all"),
    db: Session = Depends(get_db),
):
    """
    获取指定收藏夹的内容列表（分页）

    :param collection_id: 收藏夹 ID（"all" 表示全部收藏）
    :param platform: 可选平台过滤
    :param page: 页码（从 1 开始）
    :param size: 每页数量（1-2000）
    :param db: 数据库会话
    :return: 分页内容列表
    """
    try:
        items, total, next_cursor, has_more, status_counts = favorites_service.list_collection_videos(
            db, collection_id, page=page, size=size, platform=platform, cursor=cursor, status=status,
            include_status_counts=True,
        )
    except AmbiguousCollectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:  # e.g. an invalid cursor
        return {"success": False, "message": str(exc), "items": [], "total": 0}
    video_count, note_count = favorites_service.count_videos_by_kind(
        db, collection_id, platform=platform, status=status
    )
    return {
        "success": True,
        "items": items,
        "total": total,
        "video_count": video_count,
        "note_count": note_count,
        "page": page,
        "size": size,
        "next_cursor": next_cursor,
        "has_more": has_more,
        "status_counts": status_counts,
    }
