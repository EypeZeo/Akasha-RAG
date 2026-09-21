"""
收藏夹同步服务模块 (v0.7.0 Multi-Platform)

负责将从各平台抓取的快照数据与本地数据库进行差异对齐：
- 支持平台隔离与快照完整性门禁 (Partial Sync Protection)
- 新增/更新收藏夹与作品实体 (ContentItem)
- 管理多对多成员关系 (CollectionItemRelation)
- 同步持久化 IngestionItem 与 ContentPart
- 提供高性能 SQL Keyset/Offset 分页接口，杜绝内存泄漏
"""
from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import case, desc, func, select
from sqlalchemy import update as sql_update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import (
    CollectionItemRelation,
    ContentItem,
    ContentPart,
    FavoriteCollection,
    IngestionItem,
)
from app.services.chroma_service import get_chroma_service
from app.services.collection_scope import resolve_collection
from app.services.douyin_collector import (
    FavoriteScrapedCollection,
    FavoriteScrapedVideo,
    FavoriteScrapeSnapshot,
    collector,
)

logger = logging.getLogger(__name__)

ALL_COLLECTION_ID = "all"
ALL_COLLECTION_TITLE = "全部收藏"


def _utcnow() -> datetime:
    """Naive UTC datetime, matching how `func.now()` stores SQLite timestamps
    elsewhere in this schema (`datetime.utcnow()` itself is deprecated in 3.12+)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def ensure_content_item_enrichment_column(engine: Engine) -> None:
    """Idempotently extend existing SQLite installations with `last_enriched_at`.

    Same pattern as account_state.py::ensure_source_account_profile_columns --
    a plain ALTER TABLE, not a full migration.py table rebuild.
    """
    if engine.dialect.name != "sqlite":
        return
    with engine.begin() as connection:
        columns = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(content_items)")
        }
        if "last_enriched_at" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE content_items ADD COLUMN last_enriched_at DATETIME"
            )


class FavoritesService:
    """
    多平台收藏夹同步与查询服务
    """

    def save_snapshot_to_db(self, db: Session, snapshot: FavoriteScrapeSnapshot) -> dict:
        """
        将抓取的收藏夹快照持久化到本地数据库中并完成差异对齐

        :param db: 数据库会话
        :param snapshot: 抓取快照
        :return: 同步统计
        """
        platform = getattr(snapshot, "platform", "douyin")
        collections_by_platform = self._sync_collections(db, snapshot)
        added_v, removed_v, added_n, removed_n = self._sync_videos_and_cache(
            db, snapshot, collections_by_platform
        )
        db.commit()

        total_collections = (
            db.scalar(
                select(func.count()).select_from(FavoriteCollection).where(
                    FavoriteCollection.platform == platform,
                    FavoriteCollection.is_active.is_(True),
                )
            )
            or 0
        )
        total_videos = (
            db.scalar(
                select(func.count(ContentItem.id)).where(
                    ContentItem.platform == platform,
                    ContentItem.is_active.is_(True),
                )
            )
            or 0
        )

        active_videos = (
            db.scalar(
                select(func.count(ContentItem.id)).where(
                    ContentItem.platform == platform,
                    ContentItem.is_active.is_(True),
                    ContentItem.duration > 0,
                )
            )
            or 0
        )
        active_notes = (
            db.scalar(
                select(func.count(ContentItem.id)).where(
                    ContentItem.platform == platform,
                    ContentItem.is_active.is_(True),
                    (ContentItem.duration == 0) | (ContentItem.duration.is_(None)),
                )
            )
            or 0
        )
        invalid_count = getattr(snapshot, "invalid_count", 0)

        return {
            "platform": platform,
            "collections_total": int(total_collections),
            "videos_total": int(total_videos),
            "videos_count": int(active_videos),
            "notes_count": int(active_notes),
            "invalid_count": int(invalid_count),
            "added_videos": added_v,
            "removed_videos": removed_v,
            "added_notes": added_n,
            "removed_notes": removed_n,
            "added_total": added_v + added_n,
            "removed_total": removed_v + removed_n,
        }

    async def sync_from_douyin(self, db: Session, force: bool = True) -> dict:
        """
        从抖音同步收藏夹数据
        """
        snapshot = await collector.fetch_snapshot(
            max_collections=100, max_videos_per_collection=500, force=force
        )
        return self.save_snapshot_to_db(db, snapshot)

    async def sync_from_bilibili(self, db: Session) -> dict:
        """
        从 Bilibili 同步收藏夹数据（剔除失效、私密、下架视频）
        """
        import asyncio
        from app.services.bilibili.client import bilibili_client

        if not bilibili_client.is_logged_in:
            raise RuntimeError("Bilibili 账号未登录，请先在设置中扫码登录 B站")

        folders = await bilibili_client.get_user_favorites()
        collections_data: list[FavoriteScrapedCollection] = []
        videos_by_id: dict[str, FavoriteScrapedVideo] = {}

        total_folder_reported = 0
        total_fetched = 0
        invalid_items_count = 0

        for f in folders:
            folder_id = str(f.get("id"))
            title = f.get("title", "")
            media_count = int(f.get("media_count") or 0)
            total_folder_reported += media_count
            collections_data.append(
                FavoriteScrapedCollection(
                    platform_collection_id=folder_id,
                    title=title,
                    video_count=media_count,
                    cover_url=f.get("cover") or "",
                )
            )

            # 分页拉取该收藏夹下的全部视频
            pn = 1
            fetched = 0
            while True:
                resp = await bilibili_client.get_favorite_content(folder_id, pn=pn, ps=20)
                medias = resp.get("medias") or []
                fetched += len(medias)
                for m in medias:
                    bvid = m.get("bvid")
                    # 剔除失效/不可见/异常稿件
                    is_invalid = (
                        not bvid
                        or m.get("attr", 0) != 0
                        or "失效" in m.get("title", "")
                        or m.get("type") == 0
                    )
                    if is_invalid:
                        invalid_items_count += 1
                        continue

                    if bvid not in videos_by_id:
                        upper = m.get("upper") or {}
                        author_name = upper.get("name") if isinstance(upper, dict) else ""
                        videos_by_id[bvid] = FavoriteScrapedVideo(
                            platform_item_id=bvid,
                            url=f"https://www.bilibili.com/video/{bvid}",
                            title=m.get("title", ""),
                            author=author_name or "",
                            duration=int(m.get("duration") or 0),
                            collection_ids={folder_id},
                        )
                    else:
                        videos_by_id[bvid].collection_ids.add(folder_id)

                if not resp.get("has_more"):
                    break
                pn += 1
                if not medias or pn > max(1, (media_count + 19) // 20) + 2:
                    break
                await asyncio.sleep(0.05)
            total_fetched += fetched
            logger.info("B站收藏夹 [%s] 拉取完毕: 有效内容 %d 条 (元数据统计 %d 条)", title, fetched, media_count)

        # 针对视频分P探测，剔除下架/不可见稿件
        invalid_video_ids = set()

        # BUG-10/NET-04: 预读已存在的 last_enriched_at / ContentPart 快照，
        # 判断哪些视频真的需要重新调用 get_video_info。P1-3：这一步必须用
        # 独立的短生命周期只读 session，不能占着传入的 db 跨网络 await
        # 持有 SQLite 连接/事务。
        from app.db.session import session_factory

        remote_ids = list(videos_by_id.keys())
        existing_snapshot: dict[str, dict] = {}
        if remote_ids:
            with session_factory() as read_db:
                rows = read_db.execute(
                    select(ContentItem.id, ContentItem.remote_item_id, ContentItem.last_enriched_at)
                    .where(ContentItem.platform == "bilibili", ContentItem.remote_item_id.in_(remote_ids))
                ).all()
                item_ids = [r.id for r in rows]
                parts_by_item: dict[int, list[dict]] = {}
                if item_ids:
                    part_rows = read_db.execute(
                        select(ContentPart.content_item_id, ContentPart.remote_part_id,
                               ContentPart.part_index, ContentPart.part_title, ContentPart.duration)
                        .where(ContentPart.content_item_id.in_(item_ids))
                    ).all()
                    for pr in part_rows:
                        parts_by_item.setdefault(pr.content_item_id, []).append({
                            "remote_part_id": pr.remote_part_id,
                            "part_index": pr.part_index,
                            "part_title": pr.part_title,
                            "duration": pr.duration,
                        })
                for r in rows:
                    existing_snapshot[r.remote_item_id] = {
                        "last_enriched_at": r.last_enriched_at,
                        "parts": parts_by_item.get(r.id, []),
                    }
            # read_db closed here -- everything below is either in-memory or
            # the real network calls; the short-lived session never overlaps
            # with them.

        ttl = timedelta(hours=settings.bilibili_enrichment_ttl_hours)
        now = _utcnow()
        to_enrich: list[FavoriteScrapedVideo] = []
        for v in videos_by_id.values():
            snap = existing_snapshot.get(v.platform_item_id)
            is_only_placeholder = (
                snap is not None
                and len(snap["parts"]) == 1
                and snap["parts"][0]["remote_part_id"] == "default"
            )
            if (
                snap is None
                or not snap["parts"]
                or is_only_placeholder
                or snap["last_enriched_at"] is None
                or now - snap["last_enriched_at"] > ttl
            ):
                to_enrich.append(v)
            else:
                # TTL 内，已经有真实分 P 数据——复用，不重新请求。
                v.parts = [dict(p) for p in snap["parts"]]

        async def _enrich_video(v: FavoriteScrapedVideo) -> None:
            try:
                info = await bilibili_client.get_video_info(v.platform_item_id)
                pages = info.get("pages") or []
                v.parts = [
                    {
                        "remote_part_id": str(page["cid"]),
                        "part_index": int(page.get("page") or index),
                        "part_title": str(page.get("part") or f"P{index}"),
                        "duration": int(page.get("duration") or 0),
                    }
                    for index, page in enumerate(pages, start=1)
                    if page.get("cid")
                ]
                # 只有这里——get_video_info 真的成功——才允许 last_enriched_at
                # 推进；_sync_videos_and_cache 还会再要求这次持久化本身也
                # 成功才真的写这个时间戳。
                v.freshly_enriched = True
            except Exception as exc:
                err_str = str(exc)
                if "不可见" in err_str or "不存在" in err_str or "404" in err_str or "删除" in err_str:
                    logger.warning("B站视频已失效/不可见 [%s]: %s，已从同步清单排除", v.platform_item_id, exc)
                    invalid_video_ids.add(v.platform_item_id)
                else:
                    logger.warning("获取视频详情失败 [%s]: %s (降级保留基本条目)", v.platform_item_id, exc)
                    # 网络/API 失败：不能把 v.parts 清空——如果这个视频之前
                    # 已经有真实分 P 数据，清空会让下游把它当成"从来没有过
                    # 真实分 P"，插入一个多余的 default 占位行、和已有的真实
                    # parts 同时残留。失败时复用上一次已知的数据，不清空。
                    previous = existing_snapshot.get(v.platform_item_id)
                    v.parts = [dict(p) for p in previous["parts"]] if previous else []

        # 分批执行，避免冷同步（大量视频都需要富化）一次性创建成百上千个
        # 等待同一信号量的协程/任务；这限定的是这一次 sync_from_bilibili()
        # 调用自己的并发，bilibili_max_concurrency 信号量本身仍然是按事件
        # 循环存储、范围不变。
        batch_size = max(1, settings.bilibili_max_concurrency * 4)
        for start in range(0, len(to_enrich), batch_size):
            batch = to_enrich[start : start + batch_size]
            await asyncio.gather(*[_enrich_video(v) for v in batch])

        for bad_id in invalid_video_ids:
            videos_by_id.pop(bad_id, None)

        missing_diff = max(0, total_folder_reported - total_fetched)
        total_invalid_count = invalid_items_count + len(invalid_video_ids) + missing_diff

        snapshot = FavoriteScrapeSnapshot(
            collections=collections_data,
            videos=list(videos_by_id.values()),
            platform="bilibili",
            is_complete=True,
            invalid_count=total_invalid_count,
        )
        return self.save_snapshot_to_db(db, snapshot)

    def _sync_collections(
        self,
        db: Session,
        snapshot: FavoriteScrapeSnapshot,
    ) -> dict[str, FavoriteCollection]:
        """
        同步收藏夹表（严格隔离平台作用域）
        """
        platform = getattr(snapshot, "platform", "douyin")
        is_complete = getattr(snapshot, "is_complete", True)

        existing = (
            db.execute(
                select(FavoriteCollection).where(FavoriteCollection.platform == platform)
            )
            .scalars()
            .all()
        )
        existing_map = {row.remote_collection_id: row for row in existing}

        seen: set[str] = set()
        for col in snapshot.collections:
            remote_col_id = getattr(col, "remote_collection_id", None) or getattr(col, "platform_collection_id")
            seen.add(remote_col_id)
            row = existing_map.get(remote_col_id)

            if row is None:
                row = FavoriteCollection(
                    platform=platform,
                    remote_collection_id=remote_col_id,
                    title=col.title,
                    video_count=col.video_count,
                    is_active=True,
                )
                db.add(row)
                existing_map[remote_col_id] = row
            else:
                row.title = col.title
                row.video_count = col.video_count
                row.is_active = True

        # 安全门禁：仅当快照为完整抓取时，才允许将快照中未见到的收藏夹标记为失效
        if is_complete and seen:
            for row in existing:
                if row.remote_collection_id not in seen:
                    row.is_active = False

        db.flush()
        return existing_map

    def _sync_videos_and_cache(
        self,
        db: Session,
        snapshot: FavoriteScrapeSnapshot,
        collections_by_platform: dict[str, FavoriteCollection],
    ) -> tuple[int, int, int, int]:
        """
        同步内容资产表 (ContentItem)、关联表 (CollectionItemRelation) 与入库表 (IngestionItem)
        """
        platform = getattr(snapshot, "platform", "douyin")
        is_complete = getattr(snapshot, "is_complete", True)

        desired_pairs: dict[tuple[int, str], dict] = {}
        desired_video_payload: dict[str, dict] = {}

        for video in snapshot.videos:
            remote_id = video.platform_item_id
            payload = {
                "url": video.url,
                "title": video.title,
                "author": video.author,
                "duration": video.duration,
                "parts": video.parts,
                "freshly_enriched": getattr(video, "freshly_enriched", False),
            }
            desired_video_payload[remote_id] = payload

            for collection_platform_id in video.collection_ids:
                collection = collections_by_platform.get(collection_platform_id)
                if collection is None:
                    continue
                desired_pairs[(collection.id, remote_id)] = {
                    "collection_id": collection.id,
                    "remote_item_id": remote_id,
                    **payload,
                }

        # 1. 查找并同步本平台现有的 ContentItem
        existing_items = (
            db.execute(select(ContentItem).where(ContentItem.platform == platform))
            .scalars()
            .all()
        )
        existing_item_map = {item.remote_item_id: item for item in existing_items}
        existing_item_durations = {item.remote_item_id: (item.duration or 0) for item in existing_items}

        desired_video_ids = set(desired_video_payload.keys())
        existing_video_ids = set(existing_item_map.keys())

        added_ids = desired_video_ids - existing_video_ids
        removed_ids_unique = existing_video_ids - desired_video_ids

        added_videos = sum(
            1 for vid in added_ids
            if (desired_video_payload[vid].get("duration") or 0) > 0
        )
        added_notes = sum(
            1 for vid in added_ids
            if (desired_video_payload[vid].get("duration") or 0) <= 0
        )
        removed_videos = sum(
            1 for vid in removed_ids_unique
            if existing_item_durations.get(vid, 0) > 0
        )
        removed_notes = sum(
            1 for vid in removed_ids_unique
            if existing_item_durations.get(vid, 0) <= 0
        )

        # 2. 新增或更新 ContentItem
        for remote_id, payload in desired_video_payload.items():
            item = existing_item_map.get(remote_id)
            duration_val = payload["duration"] or 0
            kind = "note" if duration_val <= 0 else "video"
            canonical = (
                f"https://www.bilibili.com/video/{remote_id}"
                if platform == "bilibili"
                else f"https://www.douyin.com/video/{remote_id}"
            )
            if item is None:
                item = ContentItem(
                    platform=platform,
                    remote_item_id=remote_id,
                    canonical_url=canonical,
                    title=payload["title"],
                    author=payload["author"],
                    duration=duration_val,
                    video_url=payload["url"],
                    content_kind=kind,
                    part_count=max(1, len(payload.get("parts") or [])),
                    is_active=True,
                )
                db.add(item)
                existing_item_map[remote_id] = item
            else:
                item.title = payload["title"]
                item.author = payload["author"]
                item.duration = duration_val
                item.video_url = payload["url"]
                item.part_count = max(1, len(payload.get("parts") or []))
                item.is_active = True

        db.flush()

        # Synchronize provider pages. Two distinct cases (BUG-10/NET-04):
        #
        # 1. No real provider part data this round (Douyin never has any;
        #    Bilibili falls back here before/between successful enrichment).
        #    Keep this cheap and non-destructive: add the single "default"
        #    placeholder row if missing, otherwise just refresh its
        #    title/duration in place. This must NOT fall into case 2 below --
        #    an unrelated Douyin title edit would otherwise wipe every
        #    existing video's transcript and vectors on every sync.
        #
        # 2. Real provider parts are present (freshly fetched, or reused from
        #    cache because the enrichment TTL hasn't expired). Diff against
        #    what's actually stored using (remote_part_id, part_index,
        #    title, duration); any difference -- added/removed/renamed/
        #    re-timed parts, or upgrading from a "default" placeholder to
        #    real parts -- means the previously generated transcript/vectors
        #    no longer match today's part layout, so it must be rebuilt from
        #    scratch, not silently left stale alongside new part rows.
        existing_parts = (
            db.execute(
                select(ContentPart).where(
                    ContentPart.content_item_id.in_([it.id for it in existing_item_map.values()])
                )
            )
            .scalars()
            .all()
        )
        existing_parts_by_item: dict[int, list[ContentPart]] = {}
        for p in existing_parts:
            existing_parts_by_item.setdefault(p.content_item_id, []).append(p)

        for remote_id, item in existing_item_map.items():
            payload = desired_video_payload.get(remote_id, {})
            real_parts = payload.get("parts") or []
            existing_for_item = existing_parts_by_item.get(item.id, [])

            if not real_parts:
                default_row = next((p for p in existing_for_item if p.remote_part_id == "default"), None)
                if default_row is None:
                    db.add(ContentPart(content_item_id=item.id, remote_part_id="default", part_index=1,
                        part_title=item.title, duration=item.duration, transcript_source="whisper_asr",
                        transcript_version="1", time_range=f"0-{item.duration}"))
                else:
                    default_row.part_title = item.title
                    default_row.duration = item.duration
                    default_row.time_range = f"0-{item.duration}"
                continue

            desired_keys = {
                (str(p["remote_part_id"]), int(p["part_index"]), str(p["part_title"]), int(p["duration"]))
                for p in real_parts
            }
            existing_keys = {
                (p.remote_part_id, p.part_index, p.part_title, p.duration) for p in existing_for_item
            }

            if existing_keys == desired_keys:
                if payload.get("freshly_enriched"):
                    item.last_enriched_at = _utcnow()
                continue

            if existing_for_item:
                # A previously-known item's parts genuinely changed (added/
                # removed/renamed/re-timed, or upgrading from a "default"
                # placeholder to real parts) -- the previously generated
                # transcript/vectors no longer match today's part layout.
                # Chroma and SQLite have no shared transaction (same
                # ordering as BUG-02's clear-all): clear the vectors first,
                # so a retry after a later SQLite failure just re-clears an
                # already-empty collection instead of leaving stale vectors
                # while the DB still claims they don't exist. Flush the
                # deletes before inserting the new rows -- they can share a
                # (content_item_id, remote_part_id) key, and the unique
                # constraint would otherwise fire against the not-yet-
                # executed deletes.
                get_chroma_service().delete_by_video(remote_id, platform=platform, content_item_id=item.id)
                for stale in existing_for_item:
                    db.delete(stale)
                db.execute(
                    sql_update(IngestionItem)
                    .where(IngestionItem.content_item_id == item.id)
                    .values(status="pending", transcript_text="", summary="", error_message="")
                )
                db.flush()
            # else: brand new item, nothing existed before -- just add its
            # parts, no vectors to clear and no IngestionItem yet to revert.

            for part in real_parts:
                db.add(ContentPart(content_item_id=item.id, remote_part_id=str(part["remote_part_id"]),
                    part_index=int(part["part_index"]), part_title=str(part["part_title"]),
                    duration=int(part["duration"]), transcript_source="whisper_asr", transcript_version="1",
                    time_range=f"0-{int(part['duration'])}"))
            if payload.get("freshly_enriched"):
                item.last_enriched_at = _utcnow()

        # 3. 同步收藏夹成员关联 (CollectionItemRelation)
        active_collection_ids = [
            c.id for c in collections_by_platform.values() if c.is_active
        ]
        existing_relations = []
        if active_collection_ids:
            existing_relations = (
                db.execute(
                    select(CollectionItemRelation).where(
                        CollectionItemRelation.collection_id.in_(active_collection_ids)
                    )
                )
                .scalars()
                .all()
            )

        rel_map = {(r.collection_id, r.content_item_id): r for r in existing_relations}
        desired_rel_keys = set()

        for (col_id, remote_id) in desired_pairs.keys():
            item = existing_item_map.get(remote_id)
            if item is None:
                continue
            pair_key = (col_id, item.id)
            desired_rel_keys.add(pair_key)
            rel = rel_map.get(pair_key)
            if rel is None:
                db.add(
                    CollectionItemRelation(
                        collection_id=col_id,
                        content_item_id=item.id,
                        is_active=True,
                    )
                )
            else:
                rel.is_active = True

        # 安全防空与完整性门禁：仅在快照完整且包含有效关联时，才允许删除不再存在的成员关系
        if is_complete:
            for pair_key, rel in rel_map.items():
                if pair_key not in desired_rel_keys:
                    rel.is_active = False

        # A content item remains active while it is in at least one active
        # collection; removing the final membership must hide it from RAG.
        db.flush()
        item_ids = [item.id for item in existing_item_map.values()]
        active_link_counts: dict[int, int] = {}
        if item_ids:
            active_link_counts = dict(
                db.execute(
                    select(
                        CollectionItemRelation.content_item_id,
                        func.count(),
                    )
                    .where(
                        CollectionItemRelation.content_item_id.in_(item_ids),
                        CollectionItemRelation.is_active.is_(True),
                    )
                    .group_by(CollectionItemRelation.content_item_id)
                ).all()
            )
        for item in existing_item_map.values():
            item.is_active = active_link_counts.get(item.id, 0) > 0

        # 4. 同步 IngestionItem 表
        existing_ingestions = (
            db.execute(
                select(IngestionItem).where(
                    IngestionItem.content_item_id.in_([it.id for it in existing_item_map.values()])
                )
            )
            .scalars()
            .all()
        )
        ingestion_map = {ing.content_item_id: ing for ing in existing_ingestions}

        for remote_id in desired_video_ids:
            item = existing_item_map.get(remote_id)
            if item and item.id not in ingestion_map:
                db.add(
                    IngestionItem(
                        content_item_id=item.id,
                        pipeline_version="v0.7.0",
                        status="pending",
                    )
                )

        db.flush()
        return added_videos, removed_videos, added_notes, removed_notes

    def list_collections(self, db: Session, platform: Optional[str] = None) -> list[dict]:
        """
        获取收藏夹列表
        """
        col_query = select(FavoriteCollection).where(FavoriteCollection.is_active.is_(True))
        if platform and platform != "all":
            col_query = col_query.where(FavoriteCollection.platform == platform)

        rows = (
            db.execute(
                col_query.order_by(
                    desc(FavoriteCollection.video_count),
                    desc(FavoriteCollection.updated_at),
                )
            )
            .scalars()
            .all()
        )

        col_ids = [r.id for r in rows]
        counts_by_collection: dict[int, int] = {}
        if col_ids:
            counts_by_collection = dict(
                db.execute(
                    select(
                        CollectionItemRelation.collection_id,
                        func.count(func.distinct(CollectionItemRelation.content_item_id)),
                    )
                    .where(
                        CollectionItemRelation.collection_id.in_(col_ids),
                        CollectionItemRelation.is_active.is_(True),
                    )
                    .group_by(CollectionItemRelation.collection_id)
                ).all()
            )

        total_query = select(func.count(ContentItem.id)).where(ContentItem.is_active.is_(True))
        if platform and platform != "all":
            total_query = total_query.where(ContentItem.platform == platform)
        total = db.scalar(total_query) or 0

        result = [
            {
                "id": 0,
                "collection_id": ALL_COLLECTION_ID,
                "title": ALL_COLLECTION_TITLE,
                "video_count": int(total),
                "is_active": True,
                "platform": platform or "all",
            }
        ]
        for row in rows:
            actual_count = counts_by_collection.get(row.id, 0)
            result.append(
                {
                    "id": row.id,
                    "collection_id": row.remote_collection_id,
                    "title": row.title,
                    "cover_url": row.cover_url,
                    "video_count": actual_count,
                    "is_active": row.is_active,
                    "platform": row.platform,
                    "snapshot_revision": row.snapshot_revision,
                }
            )
        return result

    def list_videos(
        self,
        db: Session,
        collection_id: str = ALL_COLLECTION_ID,
        page: int = 1,
        size: int = 30,
        platform: Optional[str] = None,
        cursor: Optional[str] = None,
    ) -> tuple[list[dict], int, str | None, bool]:
        """
        分页获取视频列表（基于数据库直接分页，杜绝内存泄漏）
        """
        offset = (page - 1) * size
        cursor_key = self._decode_list_cursor(cursor)

        def page_rows(stmt):
            if cursor_key is not None:
                stmt = stmt.where(ContentItem.id < cursor_key)
            else:
                stmt = stmt.offset(offset)
            rows = db.execute(
                stmt.order_by(desc(ContentItem.id)).limit(size + 1)
            ).all()
            has_more = len(rows) > size
            rows = rows[:size]
            next_cursor = None
            if has_more and rows:
                next_cursor = self._encode_list_cursor(rows[-1][0].id)
            return rows, next_cursor, has_more

        if collection_id == ALL_COLLECTION_ID:
            base_stmt = (
                select(ContentItem, IngestionItem.status, IngestionItem.error_message)
                .outerjoin(IngestionItem, IngestionItem.content_item_id == ContentItem.id)
                .where(ContentItem.is_active.is_(True))
            )
            count_stmt = select(func.count(ContentItem.id)).where(ContentItem.is_active.is_(True))

            if platform and platform != "all":
                base_stmt = base_stmt.where(ContentItem.platform == platform)
                count_stmt = count_stmt.where(ContentItem.platform == platform)

            total = db.scalar(count_stmt) or 0
            rows, next_cursor, has_more = page_rows(base_stmt)

            return (
                [
                    self._to_video_dict(row, status or "pending", ALL_COLLECTION_ID, err_msg or "")
                    for row, status, err_msg in rows
                ],
                int(total),
                next_cursor,
                has_more,
            )

        # 指定收藏夹
        collection = resolve_collection(db, collection_id, platform)
        if collection is None:
            return [], 0, None, False

        base_stmt = (
            select(ContentItem, IngestionItem.status, IngestionItem.error_message)
            .join(CollectionItemRelation, CollectionItemRelation.content_item_id == ContentItem.id)
            .outerjoin(IngestionItem, IngestionItem.content_item_id == ContentItem.id)
            .where(
                CollectionItemRelation.collection_id == collection.id,
                CollectionItemRelation.is_active.is_(True),
                ContentItem.is_active.is_(True),
            )
        )
        count_stmt = (
            select(func.count(ContentItem.id))
            .join(CollectionItemRelation, CollectionItemRelation.content_item_id == ContentItem.id)
            .where(
                CollectionItemRelation.collection_id == collection.id,
                CollectionItemRelation.is_active.is_(True),
                ContentItem.is_active.is_(True),
            )
        )
        total = db.scalar(count_stmt) or 0
        rows, next_cursor, has_more = page_rows(base_stmt)

        return (
            [
                self._to_video_dict(row, status or "pending", collection_id, err_msg or "")
                for row, status, err_msg in rows
            ],
            int(total),
            next_cursor,
            has_more,
        )

    def count_videos_by_kind(
        self,
        db: Session,
        collection_id: str = ALL_COLLECTION_ID,
        platform: Optional[str] = None,
    ) -> tuple[int, int]:
        """
        统计某收藏夹范围内的 视频 / 图文 数量（整栏口径，不受分页影响）。

        判据与全代码库一致：duration > 0 为视频，duration == 0 / NULL 为图文。
        返回 (video_count, note_count)。
        """
        if collection_id == ALL_COLLECTION_ID:
            base = select(ContentItem.id, ContentItem.duration).where(ContentItem.is_active.is_(True))
            if platform and platform != "all":
                base = base.where(ContentItem.platform == platform)
        else:
            collection = resolve_collection(db, collection_id, platform)
            col_pk = collection.id if collection else None
            if col_pk is None:
                return 0, 0
            base = (
                select(ContentItem.id, ContentItem.duration)
                .join(
                    CollectionItemRelation,
                    CollectionItemRelation.content_item_id == ContentItem.id,
                )
                .where(
                    CollectionItemRelation.collection_id == col_pk,
                    CollectionItemRelation.is_active.is_(True),
                    ContentItem.is_active.is_(True),
                )
            )

        # One conditional-aggregate query instead of two separate COUNTs
        # over the same base filter (same pattern as list_pending_items).
        subq = base.subquery()
        is_note = (subq.c.duration == 0) | (subq.c.duration.is_(None))
        row = db.execute(
            select(
                func.sum(case((is_note, 1), else_=0)),
                func.sum(case((~is_note, 1), else_=0)),
            )
        ).one()
        note_count = int(row[0] or 0)
        video_count = int(row[1] or 0)
        return video_count, note_count

    @staticmethod
    def _encode_list_cursor(item_id: int) -> str:
        payload = json.dumps(item_id).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_list_cursor(cursor: Optional[str]) -> int | None:
        if not cursor:
            return None
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            raw = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
            return int(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("分页游标无效") from exc

    # 路由调用兼容别名
    list_collection_videos = list_videos

    def get_video_count(self, db: Session, platform: Optional[str] = None) -> int:
        """
        获取有效内容资产总数
        """
        stmt = select(func.count(ContentItem.id)).where(ContentItem.is_active.is_(True))
        if platform and platform != "all":
            stmt = stmt.where(ContentItem.platform == platform)
        return int(db.scalar(stmt) or 0)

    @staticmethod
    def _to_video_dict(
        item: ContentItem,
        status: str,
        collection_id: str,
        error_message: str = "",
    ) -> dict:
        """
        将 ContentItem ORM 对象转换为向后兼容字典
        """
        return {
            "id": item.id,
            "collection_id": collection_id,
            "platform_item_id": item.remote_item_id,
            "remote_item_id": item.remote_item_id,
            "platform": item.platform,
            "url": item.video_url,
            "canonical_url": item.canonical_url,
            "title": item.title,
            "author": item.author,
            "duration": item.duration,
            "part_count": item.part_count,
            "item_type": "note" if (item.duration == 0 or item.duration is None) else "video",
            "status": status,
            "error_message": error_message,
        }


# 全局单例
favorites_service = FavoritesService()
