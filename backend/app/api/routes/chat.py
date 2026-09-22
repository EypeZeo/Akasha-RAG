"""
对话路由模块

提供 RAG 问答（非流式 + SSE 流式）和会话管理接口。
"""
import json
import logging
import queue
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.db.session import get_db
from app.services.collection_scope import AmbiguousCollectionError
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
        # A request that names no unique collection is the caller's mistake, not a server fault.
        if not isinstance(exc, AmbiguousCollectionError):
            logger.exception("问答失败")
        return {
            "success": False,
            "message": str(exc),
        }


# ------------------------------------------------------------------
# SSE 流式问答
# ------------------------------------------------------------------

# answer_stream() 是一个同步生成器（内部同步调 LLM SDK），不能直接 `async
# for` 驱动而不阻塞事件循环。生产者线程负责把它完整驱动完，通过一个有界
# 队列把 (event_name, payload) 元组转交给异步的消费者；取消信号走反方向：
# 消费者检测到客户端断连后置位 cancel_event，生产者在事件之间检查它，
# 命中就对 answer_stream() 生成器调 .close()（不能打断已经在途的一次 LLM
# 网络调用，只能在下一次控制权回到生成器自己的帧时生效）。
#
# 队列满时生产者会停在 put() 里。put() 期间没人看 cancel_event，所以不能把整段
# 30 秒超时压在一次 put() 上（否则客户端断连后，生成器——连同它握着的模型流、
# 并发闸门槽位——最多还要被占 30 秒，Issue #28）；改成每隔
# _STREAM_PRODUCER_PUT_POLL_SECONDS 醒一次去看 cancel_event，见 _put_or_give_up。
_STREAM_QUEUE_MAXSIZE = 32
_STREAM_PRODUCER_PUT_TIMEOUT_SECONDS = 30.0
_STREAM_PRODUCER_PUT_POLL_SECONDS = 0.1
# cancelled / error / 结束哨兵这类收尾条目的放入时限
_STREAM_PRODUCER_NOTICE_TIMEOUT_SECONDS = 5.0
_STREAM_SENTINEL = object()
_STREAM_TERMINAL_EVENTS = ("done", "error", "cancelled")


def _put_or_give_up(
    q: "queue.Queue[Any]",
    item: Any,
    cancel_event: threading.Event,
    timeout: float,
) -> str:
    """把 item 放进队列。队列满时最多等 `timeout` 秒，但每隔
    _STREAM_PRODUCER_PUT_POLL_SECONDS 就回头看一次 cancel_event。

    返回 "ok"（放进去了）、"cancelled"（队列一直满、消费者已通知取消——
    没人会再读这个队列，不必再等）或 "timeout"（队列一直满、也没人喊取消）。
    队列有空位时无论是否已经取消都直接放入：收尾用的 cancelled 条目和结束
    哨兵靠这一点仍然进得去。
    """
    deadline = time.monotonic() + timeout
    while True:
        wait = min(_STREAM_PRODUCER_PUT_POLL_SECONDS, max(deadline - time.monotonic(), 0.0))
        try:
            q.put(item, timeout=wait)
            return "ok"
        except queue.Full:
            if cancel_event.is_set():
                return "cancelled"
            if time.monotonic() >= deadline:
                return "timeout"


def _produce_stream_events(
    gen_factory: Callable[[], Iterator[tuple[str, dict]]],
    q: "queue.Queue[Any]",
    cancel_event: threading.Event,
) -> None:
    """在独立 OS 线程运行：拉取 gen_factory() 产出的 (event, payload)，推入
    队列。每推送完一项就检查一次 cancel_event——检测粒度是"每个 SSE 事件
    之间"，不是"每个 token 之间的任意时刻"（做不到，见上）；队列满时则是
    每 _STREAM_PRODUCER_PUT_POLL_SECONDS 检查一次。
    """
    gen = gen_factory()
    try:
        for event_name, payload in gen:
            if cancel_event.is_set():
                gen.close()
                _put_or_give_up(
                    q, ("cancelled", {"message": "客户端已断开"}), cancel_event,
                    _STREAM_PRODUCER_NOTICE_TIMEOUT_SECONDS,
                )
                return
            if _put_or_give_up(
                q, (event_name, payload), cancel_event, _STREAM_PRODUCER_PUT_TIMEOUT_SECONDS,
            ) != "ok":
                # "timeout"：消费者长时间不取（网络异常但 ASGI 层还没报断连）；
                # "cancelled"：等队列腾位置的时候消费者已经走了。两种情况都不能
                # 让这个线程（和它握着的模型流）继续被占用。
                gen.close()
                return
            if event_name in _STREAM_TERMINAL_EVENTS:
                return
    except Exception as exc:
        if not isinstance(exc, AmbiguousCollectionError):
            logger.exception("流式问答生产线程异常")
        _put_or_give_up(
            q, ("error", {"message": str(exc)}), cancel_event, _STREAM_PRODUCER_NOTICE_TIMEOUT_SECONDS,
        )
    finally:
        # 保底：万一 answer_stream() 没有正常吐出 done/error 事件（比如上面
        # 的 return 分支已经处理过，这里对 Queue 的一次多余 put 是无害的
        # 幂等收尾），消费者也不会永久卡在 q.get()。
        _put_or_give_up(q, _STREAM_SENTINEL, cancel_event, _STREAM_PRODUCER_NOTICE_TIMEOUT_SECONDS)


@router.post("/ask/stream")
async def chat_ask_stream(body: AskRequest, request: Request):
    """
    SSE 流式 RAG 问答

    事件类型：
    - sources:   检索到的视频来源列表
    - delta:     LLM 逐 token 输出
    - meta:      最终元信息（session_id, route_type, latency_ms, sources）
    - done:      流结束标记
    - error:     错误信息
    - cancelled: 客户端断连后中止（仅供后端日志/自身观测，客户端此时已经
                 不在了，收不到这个事件）

    数据库会话不走 `Depends(get_db)`——它会在独立的生产者线程上通过
    `session_factory()` 自行获取，因为这个路由需要在生产者线程仍在运行期间
    持续轮询 `request.is_disconnected()`，是真正的跨线程并发访问，不能像
    一次性线程池调用那样安全复用请求作用域的 session。

    :param body: 问答请求
    :param request: 用于轮询客户端是否已断开连接
    :return: SSE 事件流
    """
    cancel_event = threading.Event()
    q: "queue.Queue[Any]" = queue.Queue(maxsize=_STREAM_QUEUE_MAXSIZE)

    def gen_factory():
        from app.db.session import session_factory

        with session_factory() as thread_db:
            yield from rag_service.answer_stream(
                thread_db, body.query, body.session_id, body.collection_id, platform=body.platform
            )

    threading.Thread(
        target=_produce_stream_events, args=(gen_factory, q, cancel_event),
        daemon=True, name="ask-stream-producer",
    ).start()

    async def event_stream():
        from starlette.concurrency import run_in_threadpool

        try:
            while True:
                if await request.is_disconnected():
                    cancel_event.set()
                    return
                try:
                    item = await run_in_threadpool(q.get, timeout=0.5)
                except queue.Empty:
                    continue
                if item is _STREAM_SENTINEL:
                    return
                event_name, payload = item
                data = json.dumps(payload, ensure_ascii=False)
                yield f"event: {event_name}\ndata: {data}\n\n"
                if event_name in _STREAM_TERMINAL_EVENTS:
                    return
        finally:
            cancel_event.set()

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
