"""Platform-partitioned ChromaDB vector storage."""
from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Iterable

from app.core.config import settings

logger = logging.getLogger(__name__)

COLLECTION_PREFIX = "akasha"
SUPPORTED_PLATFORMS = ("douyin", "bilibili")
DISTANCE_METRIC = "cosine"

_write_lock = threading.RLock()
_init_lock = threading.Lock()


class ChromaService:
    """Store each supported provider in its own durable Chroma collection."""

    def __init__(self) -> None:
        if sys.version_info >= (3, 14):
            raise RuntimeError("ChromaDB 当前不兼容 Python 3.14，请使用 Python 3.12")
        import chromadb
        self._client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
        with _write_lock:
            self._collections = {platform: self._create_collection(platform) for platform in SUPPORTED_PLATFORMS}
        logger.info("ChromaDB 初始化完成: %s (%s)", settings.chroma_persist_dir,
                    ", ".join(f"{p}={c.count()}" for p, c in self._collections.items()))

    @staticmethod
    def _collection_name(platform: str) -> str:
        return f"{COLLECTION_PREFIX}_{platform}"


    def _create_collection(self, platform: str):
        return self._client.get_or_create_collection(
            name=self._collection_name(platform), metadata={"hnsw:space": DISTANCE_METRIC, "platform": platform}
        )

    def _collection_for(self, platform: str):
        if platform not in SUPPORTED_PLATFORMS:
            raise ValueError(f"不支持的平台: {platform}")
        # Preserve old lightweight unit-test doubles. Production always has partitions.
        if not hasattr(self, "_collections"):
            return self._collection
        return self._collections[platform]

    def _target_platforms(self, platform: str | None) -> tuple[str, ...]:
        if platform is None:
            return SUPPORTED_PLATFORMS if hasattr(self, "_collections") else ("douyin",)
        self._collection_for(platform)
        return (platform,)

    def upsert_video_chunks(self, platform_item_id: str, title: str, chunks: list[str], embeddings: list[list[float]],
                            platform: str = "douyin", content_item_id: int | None = None, canonical_url: str = "",
                            part_id: int = 0, part_index: int = 0) -> list[str]:
        if not chunks:
            return []
        if len(chunks) != len(embeddings):
            raise ValueError("正文块与向量数量不匹配")
        collection = self._collection_for(platform)
        with _write_lock:
            clauses = [{"platform_item_id": platform_item_id}]
            if part_id != 0:
                clauses.append({"part_id": part_id})
            existing = collection.get(where=clauses[0] if len(clauses) == 1 else {"$and": clauses}, include=[])
            if platform == "douyin" and part_id == 0:
                chunk_ids = [f"{platform_item_id}:{idx}" for idx in range(len(chunks))]
            else:
                chunk_ids = [f"{platform}:{platform_item_id}:{part_id}:{idx}" for idx in range(len(chunks))]
            resolved_url = canonical_url or (f"https://www.bilibili.com/video/{platform_item_id}" if platform == "bilibili" else f"https://www.douyin.com/video/{platform_item_id}")
            now_ts = int(time.time())
            collection.upsert(ids=chunk_ids, embeddings=embeddings, metadatas=[{
                "chunk_id": chunk_ids[idx], "platform": platform, "platform_item_id": platform_item_id,
                "remote_item_id": platform_item_id, "content_item_id": content_item_id if content_item_id is not None else 0,
                "canonical_url": resolved_url, "part_id": part_id, "part_index": part_index,
                "chunk_index": idx, "title": title[:500], "created_at": now_ts,
            } for idx in range(len(chunks))], documents=chunks)
            stale_ids = sorted(set(existing.get("ids", [])) - set(chunk_ids))
            if stale_ids:
                collection.delete(ids=stale_ids)
        logger.info("向量入库完成: [%s] %s (part=%d), %d chunks", platform, platform_item_id, part_id, len(chunks))
        return chunk_ids

    def search(self, query_vector: list[float], top_k: int = 20, platform: str | None = None,
               scope_ids: set[str] | None = None, use_mmr: bool = True, fetch_k: int = 32,
               lambda_mult: float = 0.55) -> list[dict]:
        if top_k <= 0:
            return []
        # BUG-03: an empty (but not None) scope means "this collection has no
        # content" and must return nothing — `if scope_ids:` treats an empty
        # set the same as "no filter" and silently searches the whole store.
        if scope_ids is not None and not scope_ids:
            return []
        candidates: list[dict] = []
        n_fetch = max(fetch_k, top_k * 3)
        for target in self._target_platforms(platform):
            collection = self._collection_for(target)
            count = int(collection.count())
            if count == 0:
                continue
            query_kw = {"query_embeddings": [query_vector], "n_results": min(n_fetch, count),
                        "include": ["metadatas", "distances", "documents"]}
            if scope_ids is not None:
                query_kw["where"] = {"platform_item_id": {"$in": list(scope_ids)}}
            try:
                result = collection.query(**query_kw)
            except Exception as exc:
                if scope_ids is None:
                    raise
                logger.warning("Chroma 平台范围检索异常，使用后置过滤: %s", exc)
                query_kw.pop("where", None)
                result = collection.query(**query_kw)
            ids = (result.get("ids") or [[]])[0]
            distances = (result.get("distances") or [[]])[0]
            metadatas = (result.get("metadatas") or [[]])[0]
            documents = (result.get("documents") or [[]])[0]
            for idx, metadata in enumerate(metadatas):
                if not metadata:
                    continue
                item_id = metadata.get("platform_item_id") or metadata.get("remote_item_id")
                chunk_id = ids[idx] if idx < len(ids) else metadata.get("chunk_id")
                if not item_id or not chunk_id or (scope_ids is not None and item_id not in scope_ids):
                    continue
                source_platform = str(metadata.get("platform", target))
                url = metadata.get("canonical_url") or (f"https://www.bilibili.com/video/{item_id}" if source_platform == "bilibili" else f"https://www.douyin.com/video/{item_id}")
                candidates.append({"chunk_id": str(chunk_id), "platform": source_platform,
                    "platform_item_id": str(item_id), "remote_item_id": str(item_id),
                    "content_item_id": metadata.get("content_item_id"), "canonical_url": url,
                    "part_id": metadata.get("part_id", 0), "part_index": metadata.get("part_index", 0),
                    "title": str(metadata.get("title", "")),
                    "score": 1.0 - float(distances[idx] if idx < len(distances) else 0.0),
                    "text": str(documents[idx] if idx < len(documents) else "")})
        candidates.sort(key=lambda item: item["score"], reverse=True)
        if not candidates or not use_mmr or len(candidates) <= top_k:
            return candidates[:top_k]
        return self._mmr(candidates, top_k, lambda_mult)

    @staticmethod
    def _mmr(candidates: list[dict], top_k: int, lambda_mult: float) -> list[dict]:
        cand_sets = [set(candidate["text"][:200]) for candidate in candidates]
        selected = [0]
        remaining = list(range(1, len(candidates)))
        while remaining and len(selected) < top_k:
            best = max(remaining, key=lambda index: lambda_mult * candidates[index]["score"] - (1.0 - lambda_mult) * max(
                (len(cand_sets[index] & cand_sets[current]) / len(cand_sets[index] | cand_sets[current]))
                if (cand_sets[index] or cand_sets[current]) else 0.0 for current in selected))
            selected.append(best)
            remaining.remove(best)
        return [candidates[index] for index in selected]

    def delete_by_video(self, platform_item_id: str, platform: str | None = None, content_item_id: int | None = None) -> None:
        with _write_lock:
            for target in self._target_platforms(platform):
                clauses = [{"platform_item_id": platform_item_id}]
                if content_item_id is not None:
                    clauses.append({"content_item_id": content_item_id})
                self._collection_for(target).delete(where=clauses[0] if len(clauses) == 1 else {"$and": clauses})

    def delete_videos(self, platform_item_ids: Iterable[str], platform: str | None = None) -> None:
        item_ids = [item for item in platform_item_ids if item]
        if not item_ids:
            return
        with _write_lock:
            for target in self._target_platforms(platform):
                self._collection_for(target).delete(where={"platform_item_id": {"$in": item_ids}})

    def clear_all(self) -> None:
        with _write_lock:
            for platform in SUPPORTED_PLATFORMS:
                try:
                    self._client.delete_collection(name=self._collection_name(platform))
                except Exception:
                    pass
                self._collections[platform] = self._create_collection(platform)

    def clear_platform(self, platform: str) -> None:
        """Drop and recreate only one platform's collection, leaving the others untouched.

        Delete-then-recreate is idempotent: calling this twice (e.g. a retry
        after the caller's follow-up SQL step failed) just re-creates an
        already-empty collection, no error.
        """
        self._collection_for(platform)  # raises for an unsupported platform before touching anything
        with _write_lock:
            try:
                self._client.delete_collection(name=self._collection_name(platform))
            except Exception:
                pass
            self._collections[platform] = self._create_collection(platform)

    def count(self) -> int:
        return sum(int(self._collection_for(platform).count()) for platform in self._target_platforms(None))

    def get_video_count(self) -> int:
        ids = set()
        for platform in self._target_platforms(None):
            for metadata in self._collection_for(platform).get(include=["metadatas"]).get("metadatas", []):
                if metadata and (item_id := metadata.get("remote_item_id") or metadata.get("platform_item_id")):
                    ids.add((platform, item_id))
        return len(ids)


_chroma_service: ChromaService | None = None


def get_chroma_service() -> ChromaService:
    global _chroma_service
    if _chroma_service is None:
        with _init_lock:
            if _chroma_service is None:
                _chroma_service = ChromaService()
    return _chroma_service
