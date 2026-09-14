"""
对话路由模块

提供 RAG 问答（非流式 + SSE 流式）和会话管理接口。
"""
import json
import logging

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.db.session import get_db
from app.services.rag_service import rag_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["对话"])


class AskRequest(BaseModel):
    """
    问答请求体

    :field query: 用户问题
    :field session_id: 会话 ID（可选，不传则自动创建新会话）
    :field collection_id: 限定检索的收藏夹 ID（可选，"all" 或空表示全库检索）
    :field platform: 限定检索的平台（可选，"douyin" | "bilibili" | "all"）
    """
    query: str
    session_id: int | None = None
    collection_id: str | None = None
    platform: str | None = None


# ------------------------------------------------------------------
# 非流式问答
# ------------------------------------------------------------------

@router.post("/ask")
async def chat_ask(
    body: AskRequest, db: Session = Depends(get_db)
):
    """
    非流式 RAG 问答

    提交问题后等待完整回答返回。

    :param body: 问答请求
    :param db: 数据库会话
    :return: 完整回答 + 来源信息 + 会话 ID
    """
    try:
        result = await run_in_threadpool(
            rag_service.answer, db, body.query, body.session_id, body.collection_id, platform=body.platform
        )
        return {"success": True, **result}
    except Exception as exc:
        logger.exception("问答失败")
        return {
            "success": False,
            "message": str(exc),
        }


# ------------------------------------------------------------------
# SSE 流式问答
# ------------------------------------------------------------------

@router.post("/ask/stream")
async def chat_ask_stream(
    body: AskRequest, db: Session = Depends(get_db)
):
    """
    SSE 流式 RAG 问答

    事件类型：
    - sources: 检索到的视频来源列表
    - delta:   LLM 逐 token 输出
    - meta:    最终元信息（session_id, route_type, latency_ms, sources）
    - done:    流结束标记
    - error:   错误信息

    :param body: 问答请求
    :param db: 数据库会话
    :return: SSE 事件流
    """

    async def event_stream():
        try:
            for event_name, payload in rag_service.answer_stream(
                db, body.query, body.session_id, body.collection_id, platform=body.platform
            ):
                data = json.dumps(payload, ensure_ascii=False)
                yield f"event: {event_name}\ndata: {data}\n\n"
        except Exception as exc:
            logger.exception("流式问答异常")
            error_data = json.dumps(
                {"message": str(exc)}, ensure_ascii=False
            )
            yield f"event: error\ndata: {error_data}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ------------------------------------------------------------------
# 会话管理
# ------------------------------------------------------------------

@router.get("/sessions")
async def list_sessions(
    limit: int = Query(30, ge=1, le=100),
    q: str | None = Query(None, description="按会话标题模糊搜索"),
    db: Session = Depends(get_db),
):
    """
    获取对话会话列表，按最后消息时间降序。

    :param limit: 最大返回数量
    :param q: 可选的标题模糊搜索
    """
    items = rag_service.list_sessions(db, limit=limit, q=q)
    return {"success": True, "items": items, "total": len(items)}


class RenameSessionRequest(BaseModel):
    title: str


@router.patch("/sessions/{session_id}")
async def rename_session(
    session_id: int, body: RenameSessionRequest, db: Session = Depends(get_db)
):
    """重命名会话。"""
    ok = rag_service.rename_session(db, session_id, body.title)
    return {"success": ok, "session_id": session_id}


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(
    session_id: int,
    before: int | None = Query(None, description="仅返回 id < before 的更早消息"),
    limit: int | None = Query(None, ge=1, le=200, description="最多返回条数（配合 before 加载更早）"),
    db: Session = Depends(get_db),
):
    """
    获取指定会话的消息历史（时间升序）。

    不带参数返回全部（后兼容）。带 `limit` 返回最新 N 条；再带 `before`
    向上翻页加载更早的消息。
    """
    messages = rag_service.get_messages(db, session_id, before=before, limit=limit)
    if messages is None:
        return {
            "success": False,
            "message": f"会话不存在: {session_id}",
        }
    # 拿满一页就可能还有更早的；前端「加载更早」再取一次即可确认
    has_more = bool(limit) and len(messages) == limit
    return {
        "success": True,
        "session_id": session_id,
        "items": messages,
        "has_more": has_more,
    }


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: int, db: Session = Depends(get_db)
):
    """
    删除指定会话及其所有消息

    :param session_id: 会话 ID
    :param db: 数据库会话
    :return: 是否删除成功
    """
    ok = rag_service.delete_session(db, session_id)
    return {"success": ok, "session_id": session_id}
