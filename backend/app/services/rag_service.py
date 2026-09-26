"""
RAG 问答服务模块

核心问答流水线：
1. 查询路由（规则匹配）
2. 向量语义检索
3. 上下文构建 + 历史注入
4. LLM 生成回答（非流式 / SSE 流式）
5. 答案后处理
6. 会话与消息持久化
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
from typing import Iterable

from sqlalchemy import desc, func, tuple_
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.models.entities import (
    ChatMessage,
    ChatSession,
    VideoCache,
)
from app.services.chroma_service import get_chroma_service
from app.services.collection_scope import ContentScope, resolve_collection
from app.services.llm_service import embedding_client, llm_client

logger = logging.getLogger(__name__)

# ==================================================================
# 静态工具函数
# ==================================================================

# 中文短语无天然词边界，子串匹配本身误判率低，保留原逻辑。
_GREETING_KEYWORDS_CJK = ["你好", "在吗", "你是谁", "谢谢", "早上好", "晚安"]

# BUG-01: 纯 ASCII 问候词若也用子串匹配，"hi" 会命中 this/which/history/
# machine/architecture 等大量英文单词的一部分，把这些问题整个误判为问候语、
# 跳过检索。改成 \b 单词边界匹配，只有独立出现的 "hi"/"hello" 才算数。
_GREETING_KEYWORDS_ASCII = ["hi", "hello"]
_GREETING_ASCII_RE = re.compile(r"\b(?:" + "|".join(_GREETING_KEYWORDS_ASCII) + r")\b")


def _is_greeting(query: str) -> bool:
    """判断是否为问候语"""
    low = query.lower().strip()
    if any(kw in low for kw in _GREETING_KEYWORDS_CJK):
        return True
    return bool(_GREETING_ASCII_RE.search(low))


def _is_list_query(query: str) -> bool:
    """判断是否为列举类查询（如'有哪些视频'）"""
    return bool(
        re.search(r"有哪些|列表|清单|目录|都有什么|列出|哪些", query)
    )


def _is_summary_query(query: str) -> bool:
    """判断是否为总结类查询（如'总结一下'）"""
    return bool(
        re.search(r"总结|概述|概括|回顾|梳理|整体|全部|全库", query)
    )


def _is_structured_query(query: str) -> bool:
    """判断是否需要结构化输出"""
    return bool(
        re.search(r"总结|对比|步骤|清单|列表|框架|归纳", query)
    )


def _normalize_query(query: str) -> str:
    """规范化查询文本"""
    query = query.strip()
    query = re.sub(r"\s+", " ", query)
    return query


_FENCE_RE = re.compile(r"(```[\s\S]*?```|~~~[\s\S]*?~~~)")


def _sanitize_prose(text: str, is_structured: bool) -> str:
    """清洗单个非代码段落（原逻辑，仅作用于 fenced code 之外的文字）。"""
    # 去除 Markdown 标题符号
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)

    clean_lines: list[str] = []
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            clean_lines.append("")
            continue

        # 跳过纯分隔线
        if re.fullmatch(r"[\|\-:\s]+", line):
            continue

        # 处理表格
        if "|" in line:
            parts = [part.strip() for part in line.split("|") if part.strip()]
            line = " ".join(parts) if parts else ""
            if not line:
                continue

        # 处理无序列表
        bullet_match = re.match(r"^\s*[-*•]\s+(.*)$", line)
        if bullet_match:
            line = bullet_match.group(1).strip()

        # 去除格式标记
        line = re.sub(r"[`*_]{1,3}", "", line).strip()
        if line:
            clean_lines.append(line)

    merged = "\n".join(clean_lines)
    if not is_structured:
        merged = re.sub(r"(?<![。！？.!?：:])\n(?!\n)", " ", merged)
    return merged


def _sanitize_answer(text: str, is_structured: bool) -> str:
    """
    清洗 LLM 输出（去标题符号、压表格、合并口语段落），
    但 **完整保留 ``` 围栏代码块**（缩进、反引号、竖线、换行原样不动），
    否则代码高亮无从谈起。
    """
    text = (text or "").replace("\r\n", "\n").strip()
    if not text:
        return text

    # 按 fenced code 切分：奇数段是代码块，原样透传；偶数段按 prose 清洗。
    # 段之间统一用空行分隔，确保围栏 ``` 始终独占行首（否则不被识别为代码块）。
    parts: list[str] = []
    for i, seg in enumerate(_FENCE_RE.split(text)):
        if i % 2 == 1:
            parts.append(seg.strip())
        else:
            cleaned = _sanitize_prose(seg, is_structured).strip()
            if cleaned:
                parts.append(cleaned)
    merged = "\n\n".join(p for p in parts if p)
    merged = re.sub(r"\n{3,}", "\n\n", merged).strip()
    return merged


# ==================================================================
# RAG 服务
# ==================================================================


class RagService:
    """
    RAG 问答服务

    负责端到端的检索增强生成流程。
    """

    # ------------------------------------------------------------------
    # 路由
    # ------------------------------------------------------------------

    def _route(self, query: str, has_data: bool) -> str:
        """
        查询路由分发

        规则优先级：问候 > 列表 > 总结 > 向量检索

        :param query: 用户问题
        :param has_data: 知识库是否有数据
        :return: 路由类型（direct / db_list / db_content / vector）
        """
        if _is_greeting(query):
            return "direct"
        if _is_list_query(query):
            return "db_list"
        if _is_summary_query(query):
            # 带话题限定词的总结（如"总结技术类视频"）走语义检索
            # 纯"总结一下"/"概括"才走 db_content
            topic_words = re.sub(
                r'总结|概述|概括|回顾|梳理|整体|全部|全库|一下|帮我|一下',
                '', query
            ).strip()
            if topic_words:
                return "vector"
            return "db_content"
        if not has_data:
            return "direct"
        return "vector"

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------

    def _dense_retrieve(
        self,
        query: str,
        scope_ids: ContentScope | None = None,
        platform: str | None = None,
        top_k: int | None = None,
    ) -> list[dict]:
        """
        向量检索（支持 MMR 多样性与平台/范围过滤）

        :param query: 用户问题
        :param scope_ids: 限定检索的视频 ID 集合（None=全库）
        :param platform: 限定平台（'douyin' | 'bilibili' | None）
        :param top_k: 返回数量
        :return: 检索结果列表
        """
        # BUG-03: `scope_ids is not None` 表示"限定到某个收藏夹"，空集合
        # 表示"该收藏夹里没有内容"，必须直接返回空——不能用真值判断
        # （`if scope_ids:`），那会把"空集合"和"None/不限范围"混为一谈，
        # 导致检索悄悄退化成全库搜索。
        if scope_ids is not None and not scope_ids:
            return []

        k = top_k or settings.retrieval_top_k
        query_vector = embedding_client.embed_text(query)
        chroma = get_chroma_service()
        hits = chroma.search(
            query_vector,
            top_k=k,
            platform=platform,
            scope_ids=scope_ids,
            use_mmr=True,
            fetch_k=settings.retrieval_mmr_fetch_k,
            lambda_mult=settings.retrieval_mmr_lambda,
        )

        # 按收藏夹范围过滤（同样必须用 is not None，理由同上）
        if scope_ids is not None:
            hits = [h for h in hits if (h.get("platform", "douyin"), h["platform_item_id"]) in scope_ids]
        return hits[:k]

    def _resolve_collection_scope(
        self, db: Session, collection_id: str, platform: str | None = None
    ) -> ContentScope:
        """
        解析收藏夹 ID → 包含的 (platform, remote_item_id) 集合

        :param db: 数据库会话
        :param collection_id: 收藏夹 remote_collection_id
        :return: 内容 ID 集合
        """
        from app.models.entities import CollectionItemRelation, ContentItem
        collection = resolve_collection(db, collection_id, platform)
        if not collection:
            return set()
        rows = (
            db.query(ContentItem.platform, ContentItem.remote_item_id)
            .join(CollectionItemRelation, CollectionItemRelation.content_item_id == ContentItem.id)
            .filter(
                CollectionItemRelation.collection_id == collection.id,
                CollectionItemRelation.is_active.is_(True),
                ContentItem.is_active.is_(True),
            )
            .all()
        )
        return {(r[0], r[1]) for r in rows if r[1]}

    # ------------------------------------------------------------------
    # 上下文构建
    # ------------------------------------------------------------------

    def _build_context(
        self,
        route: str,
        hits: list[dict],
        db: Session,
        query: str = "",
        *,
        platform: str | None = None,
        scope_ids: ContentScope | None = None,
    ) -> str:
        """
        根据路由类型构建 LLM 上下文

        :param route: 路由类型
        :param hits: 检索结果（仅 vector 路由使用）
        :param db: 数据库会话
        :param query: 用户问题（用于 Map-Reduce 压缩）
        :param platform: 检索范围限定平台（db_* 路由也遵守）
        :param scope_ids: 检索范围限定的 (platform, remote_item_id) 集合
        :return: 格式化后的上下文文本
        """
        if route == "db_list":
            return self._db_list_context(db, platform=platform, scope_ids=scope_ids)
        if route == "db_content":
            # db_content 也走语义检索 + 压缩，而不只是查 DB
            if hits:
                return self._compress_chunks(query, hits[: settings.rag_context_count])
            return self._db_content_context(db, platform=platform, scope_ids=scope_ids)
        if route == "vector" and hits:
            return self._compress_chunks(
                query=query,
                hits=hits[: settings.rag_context_count]
            )
        return ""

    def _retrieve_hits_for_route(
        self,
        route: str,
        query: str,
        db: Session,
        scope_ids: ContentScope | None,
        platform: str | None,
    ) -> list[dict]:
        """给定路由决定是否要做向量检索——`answer()`/`answer_stream()` 共用
        这一段，避免两处各自维护一份、悄悄漂移出不一致的行为（BUG-08）。

        `db_content` 和 `vector` 一样做检索：有命中就走 `_compress_chunks`
        语义压缩，`_build_context` 在没有命中时会自动退回 `_db_content_context`
        的纯 DB 内容拼接，两条路由共用检索不会丢失任何一边原有的能力。
        """
        if route in ("vector", "db_content"):
            return self._filter_hits_to_done_items(db, self._dense_retrieve(query, scope_ids, platform=platform))
        return []

    @staticmethod
    def _filter_hits_to_done_items(db: Session, hits: list[dict]) -> list[dict]:
        """丢弃"整体尚未 done"的条目残留在 Chroma 里的向量命中。

        多 P 视频某一分 P 转写失败时，之前已成功的分 P 向量仍然留在 Chroma
        里可被检索——但这个 `content_item_id` 的整体入库状态是 failed，不该
        被当作可信内容返回给用户。一次批量查询状态，不逐条查库。
        """
        # `content_item_id` is `0` for legacy vectors upserted before this
        # field existed (see chroma_service.py's upsert sentinel) -- treat
        # that the same as "unknown" and pass them through unfiltered rather
        # than matching a nonexistent VideoCache row and dropping them.
        content_item_ids = {h.get("content_item_id") for h in hits if h.get("content_item_id")}
        if not content_item_ids:
            return hits
        done_ids = {
            row[0]
            for row in db.query(VideoCache.content_item_id)
            .filter(
                VideoCache.content_item_id.in_(content_item_ids),
                VideoCache.status == "done",
            )
            .all()
        }
        return [h for h in hits if h.get("content_item_id") not in content_item_ids or h.get("content_item_id") in done_ids]

    @staticmethod
    def _scoped_done_query(
        db: Session, platform: str | None, scope_ids: ContentScope | None
    ):
        """完成态入库项查询，遵守检索范围（平台 / 特定收藏夹）。"""
        from app.models.entities import ContentItem

        q = db.query(VideoCache).filter(VideoCache.status == "done")
        if platform is not None or scope_ids is not None:
            q = q.join(ContentItem, ContentItem.id == VideoCache.content_item_id)
            if platform is not None:
                q = q.filter(ContentItem.platform == platform)
            if scope_ids is not None:
                # 空集合 = 该收藏夹无内容 → 返回空，绝不回退到全库
                q = q.filter(tuple_(ContentItem.platform, ContentItem.remote_item_id).in_(scope_ids))
        return q

    def _db_list_context(
        self,
        db: Session,
        *,
        platform: str | None = None,
        scope_ids: ContentScope | None = None,
    ) -> str:
        """构建视频列表上下文（遵守检索范围）。"""
        rows = (
            self._scoped_done_query(db, platform, scope_ids)
            .limit(120)
            .all()
        )
        if not rows:
            return ""
        return "\n".join(
            f"- {row.title} ({row.platform_item_id})" for row in rows
        )

    def _db_content_context(
        self,
        db: Session,
        *,
        platform: str | None = None,
        scope_ids: ContentScope | None = None,
    ) -> str:
        """构建内容摘要上下文（遵守检索范围）。"""
        rows = (
            self._scoped_done_query(db, platform, scope_ids)
            .order_by(desc(VideoCache.processed_at))
            .limit(10)
            .all()
        )
        if not rows:
            return ""

        parts = []
        for row in rows:
            excerpt = (row.transcript_text or "")[:800]
            if not excerpt:
                continue
            parts.append(f"【{row.title}】\n{excerpt}")
        return "\n\n---\n\n".join(parts)

    def _history_context(
        self, db: Session, session_id: int | None
    ) -> str:
        """
        获取历史对话窗口

        :param db: 数据库会话
        :param session_id: 会话 ID
        :return: 格式化的历史对话
        """
        if not session_id:
            return ""

        rows = (
            db.query(ChatMessage)
            .filter(ChatMessage.session_id == session_id)
            .order_by(
                ChatMessage.created_at.desc(), ChatMessage.id.desc()
            )
            .limit(settings.chat_history_window)
            .all()
        )
        if not rows:
            return ""

        rows = list(reversed(rows))
        lines: list[str] = []
        for row in rows:
            role = "用户" if row.role == "user" else "助手"
            content = (row.content or "").strip()
            if not content:
                continue
            if len(content) > settings.chat_max_content_chars:
                content = (
                    content[: settings.chat_max_content_chars] + "..."
                )
            lines.append(f"{role}: {content}")

        return "\n".join(lines)

    def _compress_chunks(self, query: str, hits: list[dict]) -> str:
        """
        Map-Reduce 上下文压缩

        当检索到的 chunk 数 > 3 时，先对每个 chunk 独立做摘要压缩，
        再拼成最终上下文。少量 chunk（≤3）直接拼接，不增加额外 LLM 调用。

        :param query: 用户问题
        :param hits: 检索结果列表
        :return: 压缩后的上下文字符串
        """
        if len(hits) <= 3:
            return "\n\n".join(
                f"【{h['title']}】\n{h['text']}" for h in hits
            )

        compressed: list[str] = []
        for h in hits[:8]:
            try:
                summary = llm_client.chat(
                    system_prompt=(
                        "你是一个精炼的摘要员。只输出 3 条核心要点，"
                        "每条不超过 25 字。不要添加任何额外说明。"
                    ),
                    user_prompt=f"问题：{query}\n\n内容：\n{h['text']}",
                    temperature=0.1,
                    max_tokens=200,
                )
                compressed.append(f"【{h['title']}】\n{summary.strip()}")
            except Exception:
                # 压缩失败时用原始文本
                compressed.append(f"【{h['title']}】\n{h['text'][:300]}")

        # 相邻 chunk 去重：移除重复的句子开头
        deduped = [compressed[0]] if compressed else []
        for i in range(1, len(compressed)):
            prev = deduped[-1]
            curr = compressed[i]
            # 找两个 chunk 的最长公共前缀
            min_len = min(len(prev), len(curr))
            overlap_at = 0
            for j in range(20, min_len, 10):
                if prev[-j:] == curr[:j]:
                    overlap_at = j
            if overlap_at > 30:
                curr = curr[overlap_at:].lstrip("，。、；：！？\n ")
            deduped.append(curr)

        return "\n\n---\n\n".join(deduped)

    @staticmethod
    def _truncate_context(context: str, max_chars: int | None = None) -> str:
        """
        截断过长上下文

        :param context: 原始上下文
        :param max_chars: 最大字符数
        :return: 截断后的上下文
        """
        if not context:
            return ""
        cap = max_chars or settings.rag_prompt_max_context_chars
        if cap <= 0:
            return context
        if len(context) <= cap:
            return context
        return context[:cap] + "\n\n[内容过长，已截断]"

    # ------------------------------------------------------------------
    # 提示词
    # ------------------------------------------------------------------

    @staticmethod
    def _answer_style(is_structured: bool) -> str:
        """生成回答风格指令"""
        code_rule = (
            "4) 涉及代码/命令/配置时，必须放进 ```语言 围栏代码块里，"
            "保留原始换行和缩进，不要把多行代码压成一行。\n"
        )
        if is_structured:
            return (
                "输出要求：\n"
                "1) 语气自然、像对话，不要生硬模板。\n"
                "2) 可用轻结构（最多3点），每点用完整句。\n"
                "3) 禁止 Markdown 表格和标题符号。\n"
                f"{code_rule}"
            )
        return (
            "输出要求：\n"
            "1) 用自然口语化短段落回答，像真人交流。\n"
            "2) 先直接回答，再补充必要细节，段落间要有过渡。\n"
            "3) 禁止 Markdown 表格和标题符号。\n"
            f"{code_rule}"
        )

    def _build_prompts(
        self,
        route: str,
        query: str,
        context: str,
        history: str,
    ) -> tuple[str, str, bool]:
        """
        构建 System / User 提示词

        :param route: 路由类型
        :param query: 用户问题
        :param context: 检索/查询到的上下文
        :param history: 历史对话
        :return: (system_prompt, user_prompt, is_structured)
        """
        is_structured = _is_structured_query(query)
        style = self._answer_style(is_structured)
        history_block = (
            f"最近对话历史：\n{history}\n\n" if history else ""
        )

        if route == "direct":
            system = (
                "你是一个自然友好的助手，用口语化方式回答问题。\n"
                f"{style}"
            )
            user = f"{history_block}当前问题：{query}"
            return system, user, is_structured

        if route == "db_list":
            system = (
                "你是收藏夹知识库助手。用户询问视频清单/目录。"
                "先给直接结论，再列举要点。\n"
                f"{style}"
            )
            user = f"{history_block}问题：{query}\n\n已入库视频清单：\n{context}"
            return system, user, is_structured

        if route == "db_content":
            # 用纯文本标签而不是 Markdown 标题（## ...）——_sanitize_answer
            # 会无条件剥离所有 #/*/_ 标记（BUG-07），提示词只应该要求模型
            # 输出真正会保留下来的格式，不能承诺一个后处理马上会抹掉的结构。
            system = (
                "你是收藏夹知识库助手。用户要求对知识库内容做归纳总结。\n\n"
                "请按以下格式输出（用纯文本标签，不要用 Markdown 标题符号 # 或加粗 **）：\n"
                "TL;DR：\n"
                "先用 3 条 bullet 给出最核心的结论，每条不超过 30 字。\n\n"
                "共同主题：\n"
                "从所有内容中提炼跨视频的共性关键词（不超过 5 个），"
                "说明为什么这些是共同主题。\n\n"
                "各视频要点：\n"
                "逐视频列出核心观点，每个要点后标注 [来源: 标题]。"
                "如果某个视频的内容与用户问的话题无关，直接跳过不写。\n\n"
                "约束：\n"
                "- TL;DR 必须出现在最前面\n"
                "- 总字数不超过 500 字，优先讲最重要的 3 个视频\n"
                "- 每个观点必须标注来自哪个视频，格式 [来源: xxx]\n"
            )
            user = f"{history_block}问题：{query}\n\n内容片段：\n{context}"
            return system, user, is_structured

        # vector 路由 — 检测到总结类问题时追加归纳要求
        extra = ""
        if _is_summary_query(query):
            extra = (
                "\n注意用户问的是归纳总结。请：\n"
                "1. 先提炼跨视频的共同主题，再逐视频简述\n"
                "2. 每个观点后标注 [来源: 标题]\n"
                "3. 总字数不超过 500 字，只讲最相关的\n"
            )
        system = (
            "你是视频知识库问答助手。基于检索到的内容回答。"
            "先给直接结论，再简要佐证。\n"
            f"{style}{extra}"
        )
        user = f"{history_block}问题：{query}\n\n相关内容：\n{context}"
        return system, user, is_structured

    def _ensure_sources(self, answer: str, sources: list[dict]) -> str:
        """
        如果回答中没有 [来源: xxx] 标注，自动追加来源清单

        :param answer: LLM 生成的回答
        :param sources: 来源视频列表
        :return: 补充来源后的回答
        """
        if not sources:
            return answer
        if re.search(r'\[来源[:：]', answer):
            return answer

        lines = ["", "---", "📎 **参考来源：**"]
        seen: set[str] = set()
        for s in sources[:5]:
            title = s.get("title", "")
            url = s.get("url", "")
            if title and title not in seen:
                seen.add(title)
                lines.append(f"- [{title}]({url})")
        return answer + "\n" + "\n".join(lines)

    # ------------------------------------------------------------------
    # 非流式回答
    # ------------------------------------------------------------------

    def answer(
        self,
        db: Session,
        query: str,
        session_id: int | None,
        collection_id: str | None = None,
        platform: str | None = None,
    ) -> dict:
        """
        非流式 RAG 问答

        完整流程：
        路由 → 检索 → 构建上下文 → LLM 生成 → 持久化

        :param db: 数据库会话
        :param query: 用户问题
        :param session_id: 会话 ID（可选，新建会话时为空）
        :param collection_id: 限定检索的收藏夹 ID（可选，all/空=全库）
        :param platform: 限定检索的平台（可选，all/空=全平台）
        :return: {"answer": ..., "sources": ..., "session_id": ..., "route_type": ...}
        """
        started = time.perf_counter()

        # Step 1: 路由
        t0 = time.perf_counter()
        normalized = _normalize_query(query)
        chroma = get_chroma_service()
        has_data = chroma.count() > 0
        route = self._route(normalized, has_data)
        t_route = time.perf_counter() - t0

        # Step 2: 检索（支持平台与收藏夹筛选）
        t0 = time.perf_counter()
        platform_filter = platform if platform and platform not in ("all", "") else None
        # 收藏夹与内容身份都保留平台，vector 与 db_* 路由共用同一作用域。
        scope_ids: ContentScope | None = None
        if collection_id and collection_id not in ("all", ""):
            scope_ids = self._resolve_collection_scope(db, collection_id, platform_filter)
        hits = self._retrieve_hits_for_route(route, normalized, db, scope_ids, platform_filter)
        t_dense = time.perf_counter() - t0

        # Step 3: 构建上下文
        t0 = time.perf_counter()
        context = self._truncate_context(
            self._build_context(
                route, hits, db, normalized,
                platform=platform_filter, scope_ids=scope_ids,
            )
        )

        # Step 4: 历史对话
        history = self._history_context(db, session_id)
        system, user, is_structured = self._build_prompts(
            route, normalized, context, history
        )
        t_ctx = time.perf_counter() - t0

        # Step 5: LLM 生成
        t0 = time.perf_counter()
        answer = llm_client.chat(
            system_prompt=system,
            user_prompt=user,
        )
        answer = _sanitize_answer(answer, is_structured)
        # 提前构建来源列表（_ensure_sources 需要）
        sources: list[dict] = []
        seen: set[str] = set()
        for h in hits:
            vid = h["platform_item_id"]
            source_key = f"{h.get('platform', 'douyin')}:{vid}"
            if source_key not in seen:
                seen.add(source_key)
                item_plat = h.get("platform", "douyin")
                resolved_url = h.get("canonical_url")
                if not resolved_url:
                    resolved_url = (
                        f"https://www.bilibili.com/video/{vid}"
                        if item_plat == "bilibili"
                        else f"https://www.douyin.com/video/{vid}"
                    )
                sources.append({
                    "platform": item_plat,
                    "platform_item_id": vid,
                    "title": h["title"],
                    "url": resolved_url,
                    "score": round(h["score"], 4),
                })
        if route in ("db_content", "vector"):
            answer = self._ensure_sources(answer, sources)
        t_llm = time.perf_counter() - t0

        latency_ms = int((time.perf_counter() - started) * 1000)

        # Step 6: 持久化会话和消息
        session = (
            db.get(ChatSession, session_id) if session_id else None
        )
        if not session:
            title = normalized[:40] if normalized else "新对话"
            session = ChatSession(title=title)
            db.add(session)
            db.flush()

        retrieved_ids = [{
            "platform": h.get("platform", "douyin"),
            "platform_item_id": h["platform_item_id"],
            "content_item_id": h.get("content_item_id"),
            "title": h.get("title", ""),
            "url": h.get("canonical_url", ""),
            "score": round(h.get("score", 0), 4),
        } for h in hits] if hits else []
        retrieved_chunk_ids = (
            [h["chunk_id"] for h in hits] if hits else []
        )

        db.add(
            ChatMessage(
                session_id=session.id,
                role="user",
                content=query,
                route_type=route,
                retrieved_video_ids=json.dumps(retrieved_ids),
                retrieved_chunk_ids=json.dumps(retrieved_chunk_ids),
                model=settings.llm_model,
            )
        )
        db.add(
            ChatMessage(
                session_id=session.id,
                role="assistant",
                content=answer,
                route_type=route,
                retrieved_video_ids=json.dumps(retrieved_ids),
                retrieved_chunk_ids=json.dumps(retrieved_chunk_ids),
                model=settings.llm_model,
                latency_ms=latency_ms,
            )
        )
        db.commit()

        # 构建检索追踪信息
        trace = {
            "route": route,
            "steps": [
                {"name": "路由判断", "time_ms": int(t_route * 1000)},
                {"name": "向量检索", "time_ms": int(t_dense * 1000)},
                {"name": "上下文构建", "time_ms": int(t_ctx * 1000)},
                {"name": "LLM 生成", "time_ms": int(t_llm * 1000)},
            ],
            "chunks": [
                {
                    "chunk_id": h.get("chunk_id", ""),
                    "title": h.get("title", ""),
                    "score": round(h.get("score", 0), 4),
                }
                for h in hits[: settings.rag_context_count]
            ],
        }

        logger.info(
            "问答完成: route=%s, latency=%dms, hits=%d",
            route,
            latency_ms,
            len(hits),
        )
        return {
            "answer": answer,
            "sources": sources,
            "session_id": session.id,
            "route_type": route,
            "latency_ms": latency_ms,
            "trace": trace,
        }

    # ------------------------------------------------------------------
    # 流式回答
    # ------------------------------------------------------------------

    def answer_stream(
        self,
        db: Session,
        query: str,
        session_id: int | None,
        collection_id: str | None = None,
        platform: str | None = None,
        client_keys: dict[str, str] | None = None,
    ) -> Iterable[tuple[str, dict]]:
        """
        SSE 流式 RAG 问答

        先 yield sources 事件，再逐 token yield delta 事件，
        最后 yield done 事件（含 meta 信息）。

        :param db: 数据库会话
        :param query: 用户问题
        :param session_id: 会话 ID
        :param collection_id: 限定检索的收藏夹 ID（可选，all/空=全库）
        :param platform: 限定检索的平台（可选，all/空=全平台）
        :yield: (event_name, payload) 元组
        """
        started = time.perf_counter()

        # Step 1-4 与非流式一致
        t0 = time.perf_counter()
        normalized = _normalize_query(query)
        chroma = get_chroma_service()
        has_data = chroma.count() > 0
        route = self._route(normalized, has_data)
        t_route = time.perf_counter() - t0

        t1 = time.perf_counter()
        platform_filter = platform if platform and platform not in ("all", "") else None
        scope_ids: ContentScope | None = None
        if collection_id and collection_id not in ("all", ""):
            scope_ids = self._resolve_collection_scope(db, collection_id, platform_filter)
        hits = self._retrieve_hits_for_route(route, normalized, db, scope_ids, platform_filter)
        t_dense = time.perf_counter() - t1

        t2 = time.perf_counter()
        context = self._truncate_context(
            self._build_context(
                route, hits, db, normalized,
                platform=platform_filter, scope_ids=scope_ids,
            )
        )
        history = self._history_context(db, session_id)
        system, user, is_structured = self._build_prompts(
            route, normalized, context, history
        )
        t_ctx = time.perf_counter() - t2

        # 先发送 sources
        seen: set[str] = set()
        sources = []
        for h in hits:
            vid = h["platform_item_id"]
            source_key = f"{h.get('platform', 'douyin')}:{vid}"
            if source_key not in seen:
                seen.add(source_key)
                item_plat = h.get("platform", "douyin")
                resolved_url = h.get("canonical_url")
                if not resolved_url:
                    resolved_url = (
                        f"https://www.bilibili.com/video/{vid}"
                        if item_plat == "bilibili"
                        else f"https://www.douyin.com/video/{vid}"
                    )
                sources.append({
                    "platform": item_plat,
                    "platform_item_id": vid,
                    "title": h["title"],
                    "url": resolved_url,
                    "score": round(h["score"], 4),
                })
        yield ("sources", {"sources": sources})

        # Step 5: LLM 流式生成
        t3 = time.perf_counter()
        parts: list[str] = []
        try:
            stream_gen = llm_client.stream_chat(system_prompt=system, user_prompt=user)
            try:
                for delta in stream_gen:
                    if delta:
                        parts.append(delta)
                        yield ("delta", {"text": delta})
            finally:
                # 外部对 answer_stream() 生成器调 .close()（取消）时，Python
                # 会在这里注入 GeneratorExit——显式关闭内层生成器，不依赖
                # CPython 引用计数何时回收它才触发 close()。stream_chat 的
                # 闸门名额（acquire_model_call_slot）就是靠这次 close() 才
                # 能立刻释放，不是等 GC。对已耗尽的生成器调 close() 是安全
                # 的空操作。
                stream_gen.close()

            t_llm = time.perf_counter() - t3
            answer = _sanitize_answer("".join(parts), is_structured)
            latency_ms = int((time.perf_counter() - started) * 1000)

            # Step 6: 持久化
            session = (
                db.get(ChatSession, session_id) if session_id else None
            )
            if not session:
                title = normalized[:40] if normalized else "新对话"
                session = ChatSession(title=title)
                db.add(session)
                db.flush()

            retrieved_ids = [{
                "platform": h.get("platform", "douyin"),
                "platform_item_id": h["platform_item_id"],
                "content_item_id": h.get("content_item_id"),
                "title": h.get("title", ""),
                "url": h.get("canonical_url", ""),
                "score": round(h.get("score", 0), 4),
            } for h in hits] if hits else []
            retrieved_chunk_ids = (
                [h["chunk_id"] for h in hits] if hits else []
            )

            db.add(
                ChatMessage(
                    session_id=session.id,
                    role="user",
                    client_key=(client_keys or {}).get("user"),
                    content=query,
                    route_type=route,
                    retrieved_video_ids=json.dumps(retrieved_ids),
                    retrieved_chunk_ids=json.dumps(retrieved_chunk_ids),
                    model=settings.llm_model,
                )
            )
            db.add(
                ChatMessage(
                    session_id=session.id,
                    role="assistant",
                    client_key=(client_keys or {}).get("assistant"),
                    content=answer,
                    route_type=route,
                    retrieved_video_ids=json.dumps(retrieved_ids),
                    retrieved_chunk_ids=json.dumps(retrieved_chunk_ids),
                    model=settings.llm_model,
                    latency_ms=latency_ms,
                )
            )
            db.commit()

            trace = {
                "route": route,
                "steps": [
                    {"name": "路由判断", "time_ms": int(t_route * 1000)},
                    {"name": "向量检索", "time_ms": int(t_dense * 1000)},
                    {"name": "上下文构建", "time_ms": int(t_ctx * 1000)},
                    {"name": "LLM 生成", "time_ms": int(t_llm * 1000)},
                ],
                "chunks": [
                    {
                        "chunk_id": h.get("chunk_id", ""),
                        "title": h.get("title", ""),
                        "score": round(h.get("score", 0), 4),
                    }
                    for h in hits[: settings.rag_context_count]
                ],
            }

            logger.info(
                "流式问答完成: route=%s, latency=%dms, hits=%d",
                route,
                latency_ms,
                len(hits),
            )
            yield (
                "meta",
                {
                    "session_id": session.id,
                    "route_type": route,
                    "latency_ms": latency_ms,
                    "sources": sources,
                    "trace": trace,
                },
            )
            yield ("done", {"ok": True})

        except Exception as exc:
            db.rollback()
            logger.exception("流式问答失败")
            yield ("error", {"message": str(exc)})

    # ------------------------------------------------------------------
    # 会话管理
    # ------------------------------------------------------------------

    def rename_session(self, db: Session, session_id: int, title: str) -> bool:
        """重命名会话；会话不存在或标题为空返回 False。"""
        clean = (title or "").strip()[:120]
        if not clean:
            return False
        session = db.get(ChatSession, session_id)
        if session is None:
            return False
        session.title = clean
        db.commit()
        return True

    def list_sessions(
        self, db: Session, limit: int = 30, q: str | None = None
    ) -> list[dict]:
        """
        获取会话列表，按最后消息时间降序。

        :param q: 可选的标题模糊匹配（大小写不敏感）
        """
        stmt = (
            db.query(
                ChatSession,
                func.count(ChatMessage.id).label("message_count"),
                func.max(ChatMessage.created_at).label("last_message"),
            )
            .outerjoin(
                ChatMessage, ChatMessage.session_id == ChatSession.id
            )
        )
        if q and q.strip():
            stmt = stmt.filter(ChatSession.title.ilike(f"%{q.strip()}%"))
        rows = (
            stmt
            .group_by(ChatSession.id)
            .order_by(
                desc(func.max(ChatMessage.created_at)),
                desc(ChatSession.created_at),
            )
            .limit(limit)
            .all()
        )

        return [
            {
                "id": session.id,
                "title": session.title,
                "message_count": int(count or 0),
                "last_message_at": str(last_message) if last_message else None,
                "created_at": str(session.created_at),
            }
            for session, count, last_message in rows
        ]

    def get_messages(
        self,
        db: Session,
        session_id: int,
        before: int | None = None,
        limit: int | None = None,
        until: int | None = None,
    ) -> list[dict] | None:
        """
        获取会话消息历史（时间升序）。

        :param before: 仅返回 id < before 的更早消息（keyset 向上翻页）
        :param limit: 最多返回条数；配合 before 实现「加载更早」
        :return: 消息列表，会话不存在返回 None
        """
        session = db.get(ChatSession, session_id)
        if session is None:
            return None

        base = (
            db.query(ChatMessage)
            .filter(ChatMessage.session_id == session_id)
        )
        if before is not None:
            base = base.filter(ChatMessage.id < before)
        if until is not None:
            base = base.filter(ChatMessage.id <= until)

        if limit is not None:
            # 取最新的 limit 条（id 降序），再翻转成时间升序返回
            rows = list(
                reversed(base.order_by(desc(ChatMessage.id)).limit(limit).all())
            )
        else:
            rows = base.order_by(
                ChatMessage.created_at.asc(), ChatMessage.id.asc()
            ).all()

        # 批量获取关联的视频缓存信息，以精确重建消息的来源文献 (sources) 与真实原片链接
        all_vids = set()
        for row in rows:
            if row.role == "assistant" and row.retrieved_video_ids:
                try:
                    vids = json.loads(row.retrieved_video_ids)
                    if isinstance(vids, list):
                        all_vids.update(str(v) for v in vids if isinstance(v, str) and v)
                except Exception:
                    pass

        cache_map: dict[str, VideoCache | None] = {}
        if all_vids:
            caches = (
                db.query(VideoCache)
                .options(selectinload(VideoCache.content_item))
                .filter(VideoCache.platform_item_id.in_(all_vids))
                .all()
            )
            for c in caches:
                key = c.platform_item_id
                cache_map[key] = None if key in cache_map else c

        results = []
        for row in rows:
            msg_sources = []
            seen_sources = set()
            if row.role == "assistant" and row.retrieved_video_ids:
                try:
                    vids = json.loads(row.retrieved_video_ids)
                    if isinstance(vids, list):
                        for vid in vids:
                            if isinstance(vid, dict):
                                platform = str(vid.get("platform") or "douyin")
                                remote_id = str(vid.get("platform_item_id") or "")
                                key = (platform, remote_id)
                                if remote_id and key not in seen_sources:
                                    seen_sources.add(key)
                                    score = vid.get("score")
                                    msg_sources.append({
                                        "platform": platform,
                                        "platform_item_id": remote_id,
                                        "title": str(vid.get("title") or remote_id),
                                        "url": str(vid.get("url") or (
                                            f"https://www.bilibili.com/video/{remote_id}" if platform == "bilibili"
                                            else f"https://www.douyin.com/video/{remote_id}"
                                        )),
                                        **({"score": score} if isinstance(score, (int, float)) and math.isfinite(score) else {}),
                                    })
                                continue
                            c = cache_map.get(str(vid))
                            if c:
                                item = c.content_item
                                key = (item.platform if item else "douyin", str(vid))
                                if key in seen_sources:
                                    continue
                                seen_sources.add(key)
                                msg_sources.append({
                                    "platform_item_id": str(vid),
                                    "title": c.title,
                                    "platform": item.platform if item else "douyin",
                                    "url": item.canonical_url if item and item.canonical_url else f"https://www.douyin.com/video/{vid}",
                                })
                except Exception:
                    pass

            results.append({
                "id": row.id,
                "client_key": row.client_key,
                "session_id": row.session_id,
                "role": row.role,
                "content": row.content,
                "route_type": row.route_type,
                "latency_ms": row.latency_ms,
                "created_at": str(row.created_at),
                "sources": msg_sources if msg_sources else None,
            })

        return results

    def delete_session(self, db: Session, session_id: int) -> bool:
        """
        删除会话及其所有消息

        :param db: 数据库会话
        :param session_id: 会话 ID
        :return: 是否成功删除
        """
        session = db.get(ChatSession, session_id)
        if session is None:
            return False
        db.delete(session)
        db.commit()
        return True


# 全局单例
rag_service = RagService()
