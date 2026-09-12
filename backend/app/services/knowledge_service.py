"""
知识库入库服务模块

编排视频入库流水线：
1. 从 VideoCache 获取 pending 状态的视频
2. 下载音频 → ASR 转写 → 文本切块 → Embedding → 存入 ChromaDB
3. 更新 VideoCache 状态
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import case, desc, func, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import (
    CollectionItemRelation,
    ContentItem,
    ContentPart,
    FavoriteCollection,
    FavoriteVideo,
    IngestionItem,
    VideoCache,
)
from app.services.asr_service import asr_service
from app.services.chroma_service import get_chroma_service
from app.services.llm_service import embedding_client
from app.services.media_service import download_audio
from app.services.text_processing import build_fixed_chunks, clean_title_for_index
from app.services.worker import worker

logger = logging.getLogger(__name__)


class KnowledgeService:
    """
    知识库入库服务

    负责：
    1. 触发入库任务
    2. 逐视频执行入库流水线
    3. 提供知识库统计
    """

    _STATS_TTL_SECONDS = 3.0

    def __init__(self) -> None:
        self._stats_lock = threading.Lock()
        self._stats_cache: tuple[float, dict] | None = None
        self._stats_refreshing = False

    def start_sync(
        self,
        db: Session,
        scope: str = "all",
        collection_id: str | None = None,
        content_type: str = "all",
        selected_ids: list[str] | None = None,
        platform: str | None = None,
    ) -> dict:
        """
        启动入库任务（支持范围模式与指定 ID 模式）

        - 当 scope == 'selected' 时：仅入库用户显式勾选的 selected_ids；
        - 当 scope == 'all' 时：直接在数据库层原子提取当前收藏夹或全部待入库项，无需前端回传海量 ID。

        :param db: 数据库会话
        :param scope: 'all' | 'selected'
        :param collection_id: 可选，限定特定收藏夹
        :param content_type: 可选，all | video | note
        :param selected_ids: 可选，限定入库的内容 ID 列表
        :param platform: 可选，限定平台 (douyin | bilibili | all)
        :return: {"task_id": ..., "pending_count": ...}
        """
        from app.models.entities import FavoriteCollection

        if scope == "selected" and not selected_ids:
            return {"task_id": None, "pending_count": 0, "message": "未选择待入库内容"}

        eligible_statuses = ("pending", "failed") if scope == "selected" else ("pending",)
        query = (
            select(ContentItem.id)
            .join(IngestionItem, IngestionItem.content_item_id == ContentItem.id)
            .where(
                IngestionItem.status.in_(eligible_statuses),
                ContentItem.is_active.is_(True),
            )
        )

        if platform and platform != "all":
            query = query.where(ContentItem.platform == platform)

        blocked = worker.blocked_platforms()
        if blocked:
            query = query.where(ContentItem.platform.not_in(blocked))

        pending_ids: list[int] = []
        if scope == "selected" and selected_ids:
            # Selected IDs are stable local content IDs.  This removes the
            # ambiguity of same remote IDs across providers.
            selected_content_ids = [int(value) for value in selected_ids if str(value).isdigit()]
            query = query.where(ContentItem.id.in_(selected_content_ids))
            # Compatibility for an already-open pre-v0.7 browser tab.  New UI
            # sends local IDs; this fallback is platform-scoped when possible.
            if selected_content_ids and not db.scalar(select(func.count()).select_from(ContentItem).where(ContentItem.id.in_(selected_content_ids))):
                query = query.where(False)
                legacy_query = select(ContentItem.id).join(IngestionItem).where(
                    ContentItem.remote_item_id.in_(selected_ids),
                    IngestionItem.status.in_(eligible_statuses),
                    ContentItem.is_active.is_(True),
                )
                if platform and platform != "all":
                    legacy_query = legacy_query.where(ContentItem.platform == platform)
                if blocked:
                    legacy_query = legacy_query.where(ContentItem.platform.not_in(blocked))
                pending_ids = list(db.execute(legacy_query).scalars().all())
        else:
            # scope == "all": 按 collection_id 与 content_type 在数据库中全量提取
            if collection_id and collection_id != "all":
                coll = db.scalar(
                    select(FavoriteCollection.id).where(
                        FavoriteCollection.remote_collection_id == collection_id,
                        FavoriteCollection.is_active.is_(True),
                    )
                )
                if coll:
                    query = query.join(
                        CollectionItemRelation,
                        CollectionItemRelation.content_item_id == ContentItem.id,
                    ).where(
                        CollectionItemRelation.collection_id == coll,
                        CollectionItemRelation.is_active.is_(True),
                    )
                else:
                    return {
                        "task_id": None,
                        "pending_count": 0,
                        "message": "指定的收藏夹不存在",
                    }

            if content_type == "video":
                query = query.where(ContentItem.duration > 0)
            elif content_type == "note":
                query = query.where(
                    (ContentItem.duration == 0) | (ContentItem.duration.is_(None))
                )
            query = query.distinct()

        if not pending_ids:
            pending_ids = list(db.execute(query).scalars().all())

        type_label = "短视频" if content_type == "video" else ("图文笔记" if content_type == "note" else "内容")

        if not pending_ids:
            return {
                "task_id": None,
                "pending_count": 0,
                "message": f"没有待入库的{type_label}，请先同步收藏夹",
            }

        if scope == "selected":
            # The existing single-item retry button must only reset its explicit
            # failed IDs, never unrelated failures or an in-flight item.
            target_ids = pending_ids
            if target_ids:
                db.execute(
                    update(IngestionItem)
                    .where(IngestionItem.content_item_id.in_(target_ids), IngestionItem.status == "failed")
                    .values(status="pending", error_message="")
                )
                db.commit()

        task_id = str(uuid.uuid4())[:8]
        worker.submit(
            task_id,
            self._run_sync,
            pending_ids,
            progress_total=len(pending_ids),
            progress_message=f"正在初始化{type_label}入库流水线 (共 {len(pending_ids)} 个)...",
        )

        return {
            "task_id": task_id,
            "pending_count": len(pending_ids),
            "message": f"已提交入库任务，共 {len(pending_ids)} 个{type_label}",
        }

    def _run_sync(
        self,
        content_refs: list[int | str],
        task_id: str = "",
    ) -> None:
        """使用三个工作线程处理入库；数据库事务不跨越网络调用。"""
        import threading
        from concurrent.futures import ThreadPoolExecutor
        from app.db.session import session_factory
        from app.services.media_service import audio_cache_lease, clean_audio_cache
        from app.services.vision_service import vision_service

        # 重复排队和重复 ID 不能再次覆盖已完成的正文、向量或失败原因。
        item_ids = iter(dict.fromkeys(content_refs))
        total = len(set(content_refs))
        if not total:
            return

        chroma = get_chroma_service()
        completed_count = 0
        failed_count = 0
        progress_lock = threading.Lock()

        def report(message: str) -> None:
            with progress_lock:
                worker.update_progress(task_id, completed_count, total, message)

        def save_state(content_ref: int | str, **values) -> None:
            # 转写正文一旦变更，之前缓存的 AI 整理（summary）随即失效，一并清空。
            if "transcript_text" in values and "summary" not in values:
                values["summary"] = ""
            with session_factory() as db:
                if isinstance(content_ref, int):
                    cid = content_ref
                else:  # compatibility for pre-v0.7 queued in-memory tasks
                    cid = db.scalar(select(ContentItem.id).where(ContentItem.remote_item_id == str(content_ref)))
                if cid:
                    db.execute(
                        update(IngestionItem)
                        .where(IngestionItem.content_item_id == cid)
                        .values(**values)
                    )
                    db.commit()

        def bail_if_cancelled(
            content_ref: int | str,
            claimed: bool,
            item_platform: str | None = None,
        ) -> bool:
            """在昂贵/收费节点前检查取消：已认领的项回退到 pending，返回 True 表示应中止。"""
            if not worker.is_cancelled(task_id) and not worker.is_platform_blocked(item_platform):
                return False
            if claimed:
                try:
                    save_state(content_ref, status="pending", error_message="已取消入库")
                except Exception:
                    pass
            return True

        def process_one_video(content_ref: int | str) -> None:
            nonlocal completed_count, failed_count
            claimed = False
            try:
                if bail_if_cancelled(content_ref, False):
                    return
                with session_factory() as db:
                    item = db.execute(
                        select(
                            ContentItem.platform,
                            ContentItem.remote_item_id,
                            ContentItem.title,
                            ContentItem.canonical_url,
                            IngestionItem.transcript_text,
                            ContentItem.id.label("content_item_id"),
                            ContentItem.duration,
                            ContentItem.author,
                            ContentItem.cover_url,
                            IngestionItem.id.label("ingestion_id"),
                        )
                        .select_from(ContentItem)
                        .join(
                            IngestionItem,
                            IngestionItem.content_item_id == ContentItem.id,
                        )
                        .where(ContentItem.id == content_ref if isinstance(content_ref, int) else ContentItem.remote_item_id == str(content_ref))
                    ).first()
                    if item is None:
                        logger.warning("ContentItem / IngestionItem 记录不存在: %s", content_ref)
                        return
                    item_platform = item.platform or "douyin"
                    platform_item_id = item.remote_item_id
                    title = item.title or ""
                    author = item.author or ""
                    cover_url = item.cover_url
                    canonical_url = item.canonical_url or ""
                    is_note = item_platform == "douyin" and item.duration in (0, None)
                    transcript_text = item.transcript_text or ""
                    has_checkpoint = len(transcript_text.strip()) >= 10
                    # 比较并更新必须处于同一条 SQL，旧队列跳过已处理/被占用项。
                    result = db.execute(
                        update(IngestionItem)
                        .where(
                            IngestionItem.id == item.ingestion_id,
                            IngestionItem.status == "pending",
                        )
                        .values(
                            status="transcribing" if is_note or has_checkpoint else "downloading",
                            error_message="",
                        )
                    )
                    db.commit()
                    claimed = result.rowcount == 1
                if not claimed:
                    return

                type_tag = "图文" if is_note else ("B站视频" if item_platform == "bilibili" else "视频")
                part_transcripts: list[tuple[ContentPart, str]] = []
                if bail_if_cancelled(content_ref, claimed, item_platform):
                    return
                if has_checkpoint:
                    report(f"继续向量化 ({type_tag}): {title[:22]}...")
                elif is_note:
                    report(f"🖼️ 抓取图集: {title[:22]}...")
                    img_urls = vision_service.fetch_note_image_urls(platform_item_id, cover_url)
                    extracted_text = ""
                    if img_urls:
                        report(f"🖼️ Qwen-VL解析({len(img_urls)}图): {title[:18]}...")
                        extracted_text = vision_service.extract_text_from_images(img_urls, title)
                    clean_title = clean_title_for_index(title)
                    if extracted_text and len(extracted_text.strip()) >= 10:
                        transcript_text = f"【图文笔记全文】标题与文案：{clean_title}\n\n{extracted_text}"
                    elif len(clean_title) >= 20:
                        transcript_text = f"【图文笔记全文】标题与正文：\n{clean_title}"
                    else:
                        transcript_text = ""
                elif item_platform == "bilibili":
                    report(f"📺 提取 B站正文 (字幕/语音): {title[:22]}...")
                    save_state(content_ref, status="downloading")
                    import asyncio
                    from app.services.bilibili.client import bilibili_client
                    from app.services.bilibili.content_fetcher import bilibili_content_fetcher
                    with session_factory() as part_db:
                        parts = part_db.scalars(select(ContentPart).where(
                            ContentPart.content_item_id == item.content_item_id
                        ).order_by(ContentPart.part_index)).all()
                    if not parts:
                        raise RuntimeError("B站视频缺少分P元数据，请先重新同步收藏夹")

                    async def _fetch_all_parts():
                        # 一次 asyncio.run 里顺序处理全部分 P（保持原有的逐 P
                        # 顺序行为不变），而不是每个分 P 各开关一次事件循环——
                        # 后者会让 bilibili_client 在每次 asyncio.run 内都新
                        # 建一个绑定当前循环的 AsyncClient，循环销毁时这个
                        # client 从未被关闭，连接被悬空丢弃（BUG-04）。
                        results = []
                        try:
                            for p in parts:
                                text = await bilibili_content_fetcher.fetch_transcript(
                                    bvid=platform_item_id, cid=int(p.remote_part_id), title=title,
                                    part_title=p.part_title,
                                )
                                results.append((p, text))
                        finally:
                            await bilibili_client.aclose()
                        return results

                    part_transcripts.extend(asyncio.run(_fetch_all_parts()))
                    transcript_text = "\n\n".join(text for _, text in part_transcripts)
                    if bail_if_cancelled(content_ref, claimed, item_platform):
                        return
                    save_state(content_ref, transcript_text=transcript_text, status="transcribing")
                else:
                    # 视频封面不能替代音频正文；下载异常直接保留原始原因。
                    report(f"📹 下载音频: {title[:22]}...")
                    with audio_cache_lease(platform_item_id):
                        audio_path = download_audio(
                            f"https://www.douyin.com/video/{platform_item_id}",
                            platform_item_id,
                        )
                        if not audio_path or not audio_path.is_file():
                            raise RuntimeError("音频下载未生成可用文件")
                        if bail_if_cancelled(content_ref, claimed, item_platform):
                            return
                        save_state(content_ref, status="transcribing")
                        report(f"📹 语音转写: {title[:22]}...")
                        transcript_text = asr_service.transcribe_to_text(
                            audio_path,
                            cancel_check=lambda: (
                                worker.is_cancelled(task_id)
                                or worker.is_platform_blocked(item_platform)
                            ),
                        )
                        if not transcript_text or len(transcript_text.strip()) < 10:
                            raise RuntimeError("ASR 未能提取到实质音频正文（已拒绝仅标题入库）")
                        # 在收费 ASR 完成后先提交检查点，Embedding 失败可以直接续传。
                        save_state(content_ref, transcript_text=transcript_text)
                        try:
                            audio_path.unlink(missing_ok=True)
                        except OSError as exc:
                            logger.warning("已保存转写，音频缓存暂未清理 [%s]: %s", platform_item_id, exc)

                if not transcript_text or len(transcript_text.strip()) < 10:
                    raise RuntimeError(f"未能提取到{type_tag}正文内容（已拒绝仅标题入库）")
                if is_note and not has_checkpoint:
                    save_state(content_ref, transcript_text=transcript_text)

                if part_transcripts:
                    # Each Bilibili page has independent vector IDs/metadata,
                    # preserving P-specific provenance and avoiding P1-only RAG.
                    for part, part_text in part_transcripts:
                        chunks = build_fixed_chunks(part_text)
                        if not chunks:
                            raise RuntimeError(f"P{part.part_index} 正文切块为空")
                        header = f"B站视频标题：{clean_title_for_index(title)}\n分P：{part.part_title}"
                        if author:
                            header += f"\n作者：{author}"
                        if header not in chunks:
                            chunks = [header] + chunks
                        embeddings = embedding_client.embed_texts(chunks)
                        if len(embeddings) != len(chunks) or any(not vector for vector in embeddings):
                            raise RuntimeError("Embedding 返回的向量数量或内容与正文切块不匹配")
                        chroma.upsert_video_chunks(
                            platform_item_id=platform_item_id, title=title, chunks=chunks, embeddings=embeddings,
                            platform=item_platform, content_item_id=item.content_item_id,
                            canonical_url=canonical_url, part_id=int(part.id), part_index=part.part_index,
                        )
                    save_state(content_ref, status="done", error_message="", processed_at=datetime.now(timezone.utc))
                    logger.info("入库成功 (B站多P): %s (%d parts)", platform_item_id, len(part_transcripts))
                    return

                chunks = build_fixed_chunks(transcript_text)
                if not chunks:
                    raise RuntimeError("正文文本切块为空")
                meta_chunk = f"{type_tag}标题：{clean_title_for_index(title)}"
                if author:
                    meta_chunk += f"\n作者：{author}"
                if meta_chunk not in chunks:
                    chunks = [meta_chunk] + chunks

                if bail_if_cancelled(content_ref, claimed, item_platform):
                    return
                report(f"✨ 向量化入库 ({type_tag}): {title[:20]}...")
                embeddings = embedding_client.embed_texts(chunks)
                if len(embeddings) != len(chunks) or any(not vector for vector in embeddings):
                    raise RuntimeError("Embedding 返回的向量数量或内容与正文切块不匹配")
                chroma.upsert_video_chunks(
                    platform_item_id=platform_item_id,
                    title=title,
                    chunks=chunks,
                    embeddings=embeddings,
                    platform=item_platform,
                    content_item_id=item.content_item_id,
                    canonical_url=canonical_url,
                )
                save_state(
                    content_ref,
                    status="done",
                    error_message="",
                    processed_at=datetime.now(timezone.utc),
                )
                logger.info("入库成功 (%s): %s (%d chunks)", type_tag, platform_item_id, len(chunks))
            except Exception as exc:
                logger.exception("入库失败: %s", content_ref)
                with progress_lock:
                    failed_count += 1
                if claimed:
                    try:
                        save_state(content_ref, status="failed", error_message=str(exc)[:1000])
                    except Exception:
                        logger.exception("无法保存入库失败状态: %s", content_ref)
            finally:
                with progress_lock:
                    completed_count += 1
                    worker.update_progress(
                        task_id, completed_count, total,
                        f"已处理 {completed_count}/{total} 个内容，失败 {failed_count} 个",
                    )

        def consume_items() -> None:
            from app.services.douyin_media_resolver import close_thread_local_browser
            try:
                while True:
                    if worker.is_cancelled(task_id):
                        return
                    with progress_lock:
                        content_ref = next(item_ids, None)
                    if content_ref is None:
                        return
                    process_one_video(content_ref)
            finally:
                # 确定性回收本工作线程复用的 headless Chromium，杜绝僵尸进程
                close_thread_local_browser()

        max_workers = min(3, total)
        logger.info("启动知识库入库流水线: 共 %d 个内容，工作线程数: %d", total, max_workers)
        report(f"正在启动入库流水线 (共 {total} 个内容)...")
        from concurrent.futures import ALL_COMPLETED
        from concurrent.futures import wait as _wait_futures

        executor = ThreadPoolExecutor(max_workers=max_workers)
        futures = [executor.submit(consume_items) for _ in range(max_workers)]
        try:
            # 固定三个消费者，避免为上万作品一次性创建上万个 Future。
            # 轮询等待而非 future.result() 阻塞，以便取消后能在 90s 内收尾——
            # 正在处理的项目跑完当前步骤即停；真卡死的下载/解析线程不拖住收尾。
            cancel_deadline = None
            pending = set(futures)
            while pending:
                done, pending = _wait_futures(pending, timeout=3, return_when=ALL_COMPLETED)
                for fut in done:
                    exc = fut.exception()
                    if exc and not worker.is_cancelled(task_id):
                        raise exc
                if worker.is_cancelled(task_id):
                    if cancel_deadline is None:
                        cancel_deadline = time.monotonic() + 90
                    elif time.monotonic() > cancel_deadline:
                        logger.warning("取消收尾超时，%d 个工作线程可能仍在结束当前调用", len(pending))
                        break
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
            if worker.is_cancelled(task_id):
                # 把本批残留的「下载中/转写中」回退为待入库
                try:
                    with session_factory() as db:
                        cids = db.scalars(
                            select(ContentItem.id).where(ContentItem.id.in_([r for r in content_refs if isinstance(r, int)]))
                        ).all()
                        if cids:
                            db.execute(
                                update(IngestionItem)
                                .where(
                                    IngestionItem.content_item_id.in_(cids),
                                    IngestionItem.status.in_(("downloading", "transcribing")),
                                )
                                .values(status="pending", error_message="已取消入库")
                            )
                            db.commit()
                except Exception as exc:
                    logger.warning("取消后回退残留状态异常: %s", exc)
            try:
                clean_audio_cache()
            except Exception as exc:
                logger.warning("任务结束后清理音频缓存异常: %s", exc)
        if worker.is_cancelled(task_id):
            logger.info("知识库入库流水线已取消: 已处理 %d/%d", completed_count, total)
            return
        if failed_count:
            raise RuntimeError(f"入库处理结束：{failed_count}/{total} 个内容失败，请查看作品失败原因后重试")
        logger.info("知识库入库流水线全部执行完毕: 共 %d 个内容", total)

    def get_stats(self, db: Session) -> dict:
        """Return a short-lived stale value while one request refreshes it."""
        now = time.monotonic()
        with self._stats_lock:
            cached = self._stats_cache
            if cached and now - cached[0] < self._STATS_TTL_SECONDS:
                return dict(cached[1])
            if cached and self._stats_refreshing:
                return dict(cached[1])
            self._stats_refreshing = True
        try:
            value = self._compute_stats(db)
        finally:
            with self._stats_lock:
                self._stats_refreshing = False
        with self._stats_lock:
            self._stats_cache = (time.monotonic(), value)
        return dict(value)

    def _compute_stats(self, db: Session) -> dict:
        """
        获取知识库统计信息

        :param db: 数据库会话
        :return: 统计数据
        """
        # 各状态视频/内容数量
        pending = db.scalar(
            select(func.count()).select_from(IngestionItem).where(
                IngestionItem.status == "pending"
            )
        ) or 0
        done = db.scalar(
            select(func.count()).select_from(IngestionItem).where(
                IngestionItem.status == "done"
            )
        ) or 0
        failed = db.scalar(
            select(func.count()).select_from(IngestionItem).where(
                IngestionItem.status == "failed"
            )
        ) or 0
        downloading = db.scalar(
            select(func.count()).select_from(IngestionItem).where(
                IngestionItem.status == "downloading"
            )
        ) or 0
        transcribing = db.scalar(
            select(func.count()).select_from(IngestionItem).where(
                IngestionItem.status == "transcribing"
            )
        ) or 0

        # 细分统计视频与图文数量
        total_video = db.scalar(
            select(func.count(ContentItem.id)).where(
                ContentItem.is_active.is_(True), ContentItem.duration > 0
            )
        ) or 0
        total_note = db.scalar(
            select(func.count(ContentItem.id)).where(
                ContentItem.is_active.is_(True),
                (ContentItem.duration == 0) | (ContentItem.duration.is_(None)),
            )
        ) or 0

        # 细分统计：视频与图文各状态 (通过 join ContentItem)
        v_pending = db.scalar(
            select(func.count(IngestionItem.id))
            .join(ContentItem, ContentItem.id == IngestionItem.content_item_id)
            .where(ContentItem.is_active.is_(True), IngestionItem.status == "pending", ContentItem.duration > 0)
        ) or 0
        v_done = db.scalar(
            select(func.count(IngestionItem.id))
            .join(ContentItem, ContentItem.id == IngestionItem.content_item_id)
            .where(ContentItem.is_active.is_(True), IngestionItem.status == "done", ContentItem.duration > 0)
        ) or 0
        v_failed = db.scalar(
            select(func.count(IngestionItem.id))
            .join(ContentItem, ContentItem.id == IngestionItem.content_item_id)
            .where(ContentItem.is_active.is_(True), IngestionItem.status == "failed", ContentItem.duration > 0)
        ) or 0

        n_pending = db.scalar(
            select(func.count(IngestionItem.id))
            .join(ContentItem, ContentItem.id == IngestionItem.content_item_id)
            .where(
                ContentItem.is_active.is_(True),
                IngestionItem.status == "pending",
                (ContentItem.duration == 0) | (ContentItem.duration.is_(None)),
            )
        ) or 0
        n_done = db.scalar(
            select(func.count(IngestionItem.id))
            .join(ContentItem, ContentItem.id == IngestionItem.content_item_id)
            .where(
                ContentItem.is_active.is_(True),
                IngestionItem.status == "done",
                (ContentItem.duration == 0) | (ContentItem.duration.is_(None)),
            )
        ) or 0
        n_failed = db.scalar(
            select(func.count(IngestionItem.id))
            .join(ContentItem, ContentItem.id == IngestionItem.content_item_id)
            .where(
                ContentItem.is_active.is_(True),
                IngestionItem.status == "failed",
                (ContentItem.duration == 0) | (ContentItem.duration.is_(None)),
            )
        ) or 0

        # ChromaDB 统计
        chroma = get_chroma_service()
        chroma_chunks = chroma.count()
        # Reading all Chroma metadata just to count distinct videos becomes an
        # O(N) memory spike at desktop-scale collections.  A completed durable
        # ingestion row is the authoritative count for this UI statistic.
        chroma_videos = int(done)

        return {
            "video_cache": {
                "pending": int(pending),
                "downloading": int(downloading),
                "transcribing": int(transcribing),
                "done": int(done),
                "failed": int(failed),
                "total_video": int(total_video),
                "total_note": int(total_note),
            },
            "detail": {
                "video": {
                    "total": int(total_video),
                    "done": int(v_done),
                    "pending": int(v_pending),
                    "failed": int(v_failed),
                },
                "note": {
                    "total": int(total_note),
                    "done": int(n_done),
                    "pending": int(n_pending),
                    "failed": int(n_failed),
                },
            },
            "content_types": {
                "total_video": int(total_video),
                "total_note": int(total_note),
            },
            "chromadb": {
                "total_chunks": chroma_chunks,
                "total_videos": chroma_videos,
            },
        }

    def get_progress(self, task_id: str) -> dict | None:
        """
        查询入库任务进度

        :param task_id: 任务 ID
        :return: 进度信息
        """
        return worker.get_progress(task_id)

    def cancel_sync(self, task_id: str) -> bool:
        """请求取消一个入库任务；正在处理的项目会结束当前步骤后停止。"""
        return worker.cancel(task_id)

    def list_pending_items(
        self,
        db: Session,
        collection_id: str | None = None,
        content_type: str = "all",
        page: int = 1,
        page_size: int = 50,
        platform: str | None = None,
    ) -> dict:
        """
        分批获取待入库的内容列表（支持海量数据按需分页与全局计数分离）

        :param db: 数据库会话
        :param collection_id: 可选，限定特定收藏夹（'all' 或 None 表示全部）
        :param content_type: 可选，'all' | 'video' | 'note'
        :param page: 页码（从 1 开始）
        :param page_size: 每页大小（默认 50，最大 100）
        :param platform: 可选，限定平台 (douyin | bilibili | all)
        :return: 包含 items, total, video_count, note_count, page, page_size, has_more 的字典
        """
        page = max(1, page)
        page_size = max(1, min(100, page_size))

        base_query = (
            select(
                ContentItem.id.label("content_item_id"),
                ContentItem.remote_item_id.label("platform_item_id"),
                ContentItem.platform,
                ContentItem.title,
                IngestionItem.status,
                ContentItem.author,
                ContentItem.duration,
                ContentItem.cover_url,
                ContentItem.updated_at.label("latest_updated"),
            )
            .join(
                IngestionItem,
                IngestionItem.content_item_id == ContentItem.id,
            )
            .where(
                IngestionItem.status == "pending",
                ContentItem.is_active.is_(True),
            )
        )

        if platform and platform != "all":
            base_query = base_query.where(ContentItem.platform == platform)

        if collection_id and collection_id != "all":
            coll = db.scalar(
                select(FavoriteCollection.id).where(
                    FavoriteCollection.remote_collection_id == collection_id,
                    FavoriteCollection.is_active.is_(True),
                )
            )
            if coll:
                base_query = base_query.join(
                    CollectionItemRelation,
                    CollectionItemRelation.content_item_id == ContentItem.id,
                ).where(
                    CollectionItemRelation.collection_id == coll,
                    CollectionItemRelation.is_active.is_(True),
                )
            else:
                return {
                    "items": [],
                    "total": 0,
                    "video_count": 0,
                    "note_count": 0,
                    "page": page,
                    "page_size": page_size,
                    "has_more": False,
                }

        base_grouped = base_query.group_by(ContentItem.id)
        subq = base_grouped.subquery()

        # 计算全局各分类总数（在当前 collection 作用域内）
        count_stats = db.execute(
            select(
                func.count().label("total"),
                func.sum(case((subq.c.duration > 0, 1), else_=0)).label("video_count"),
                func.sum(case((or_(subq.c.duration == 0, subq.c.duration.is_(None)), 1), else_=0)).label("note_count"),
            )
        ).one()

        total_all = count_stats.total or 0
        total_video = int(count_stats.video_count or 0)
        total_note = int(count_stats.note_count or 0)

        # 根据 content_type 确定当前列表的分页总数与过滤
        items_query = select(subq)
        if content_type == "video":
            items_query = items_query.where(subq.c.duration > 0)
            current_total = total_video
        elif content_type == "note":
            items_query = items_query.where(
                (subq.c.duration == 0) | (subq.c.duration.is_(None))
            )
            current_total = total_note
        else:
            current_total = total_all

        # 排序与分页切片
        offset = (page - 1) * page_size
        items_query = (
            items_query
            .order_by(desc(subq.c.latest_updated), desc(subq.c.platform_item_id))
            .offset(offset)
            .limit(page_size)
        )

        rows = db.execute(items_query).all()

        items = []
        for r in rows:
            is_note = (r.duration == 0 or r.duration is None)
            items.append({
                "id": r.content_item_id,
                "platform_item_id": r.platform_item_id,
                "platform": getattr(r, "platform", "douyin"),
                "title": r.title,
                "author": r.author,
                "duration": r.duration or 0,
                "item_type": "note" if is_note else "video",
                "cover_url": r.cover_url or "",
                "status": r.status or "pending",
            })

        has_more = (offset + len(items)) < current_total

        return {
            "items": items,
            "total": current_total,
            "video_count": total_video,
            "note_count": total_note,
            "page": page,
            "page_size": page_size,
            "has_more": has_more,
        }

    def delete_video(self, db: Session, platform_item_id: str, platform: str | None = None) -> dict:
        """
        删除已入库内容

        清理 ChromaDB 向量、重置 IngestionItem 状态、删除音频缓存。

        :param db: 数据库会话
        :param platform_item_id: 平台内容 ID
        :return: 删除结果
        :raises ValueError: 内容不存在或正在入库中
        """
        item_query = (
            select(ContentItem, IngestionItem)
            .join(IngestionItem, IngestionItem.content_item_id == ContentItem.id)
            .where(ContentItem.remote_item_id == platform_item_id)
        )
        if platform and platform != "all":
            item_query = item_query.where(ContentItem.platform == platform)
        matches = db.execute(item_query).all()
        if len(matches) > 1:
            raise ValueError("内容 ID 在多个平台存在，请指定平台后重试")
        item = matches[0] if matches else None

        if item is None:
            raise ValueError(f"内容 {platform_item_id} 不存在")
        content_item, ingestion = item
        if ingestion.status in ("downloading", "transcribing"):
            raise ValueError("内容正在入库中，无法删除")

        # 1. 删除 ChromaDB 向量
        chroma = get_chroma_service()
        chroma.delete_by_video(platform_item_id, platform=content_item.platform, content_item_id=content_item.id)

        # 2. 重置 IngestionItem
        ingestion.status = "pending"
        ingestion.transcript_text = ""
        ingestion.error_message = ""
        ingestion.processed_at = None

        # 3. 清理音频缓存
        audio_dir = Path(settings.audio_cache_dir)
        for ext in (".mp3", ".wav", ".m4a", ".webm", ".opus"):
            audio_file = audio_dir / f"{platform_item_id}{ext}"
            if audio_file.exists():
                audio_file.unlink(missing_ok=True)

        db.commit()
        logger.info("内容已重置为待入库: %s", platform_item_id)
        return {"platform_item_id": platform_item_id, "platform": content_item.platform, "status": "pending"}


# 全局单例
knowledge_service = KnowledgeService()
