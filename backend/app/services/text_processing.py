"""
文本处理服务模块

提供文本清洗、近似 Token 计数、文本切块功能。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.config import settings


@dataclass
class RawSegment:
    """
    ASR 转写片段

    表示音频中一段连续的语音转写结果。
    """

    text: str
    """转写文本"""

    start_ms: int
    """开始时间（毫秒）"""

    end_ms: int
    """结束时间（毫秒）"""

    lang: str
    """语言代码（如 zh / en）"""


@dataclass
class ChunkResult:
    """
    文本切块结果

    表示一段清洗后的、大小适中的文本块。
    """

    text: str
    """文本内容"""

    token_count: int
    """近似 Token 数量"""

    start_ms: int
    """原始音频开始时间（毫秒），无时间戳则为 0"""

    end_ms: int
    """原始音频结束时间（毫秒），无时间戳则为 0"""

    lang: str
    """语言代码"""


def clean_text(text: str) -> str:
    """
    清洗 ASR 转写文本

    执行以下清洗操作：
    - 全角空格转半角
    - 合并连续空白字符
    - 移除连续的无意义语气词

    :param text: 原始转写文本
    :return: 清洗后的文本
    """
    text = text.replace("　", " ")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"(啊|嗯|呃|哦|额){2,}", "", text)
    return text.strip()


# 仅匹配「前面有正文内容」时，末尾连续 >= 2 个的引流 tag 串（lookbehind 保证不吃到句首 tag）
_TRAILING_HASHTAG_RUN = re.compile(r"(?<=\S)(?:\s+#[^\s#]+){2,}\s*$")
_HASHTAG_TOKEN = re.compile(r"#([^\s#]+)")


def clean_title_for_index(title: str) -> str:
    """
    为向量入库清洗标题里的引流话题标签，尽量保留主题信息。

    抖音标题取自文案 desc，末尾常堆砌一串引流 tag（``... #交易 #期货 #交易心得``），
    句首/句中也可能出现作为主题词的 tag（``#Python 进阶：写装饰器``）。

    策略：
    1. 只删「末尾连续 >= 2 个」的纯引流 tag 串；
    2. 剩余文本里的 ``#`` 号去掉但保留词本身（句首主题 tag 不整词删除）；
    3. 防空兜底——若第 1 步把整条标题都删空了（说明全是 tag），改为仅去 ``#`` 保留所有词。

    仅用于生成入库向量内容，不影响展示用的原始标题。

    :param title: 原始标题
    :return: 清洗后的标题（保证非空，除非入参本身为空）
    """
    if not title or not title.strip():
        return ""

    stripped = _TRAILING_HASHTAG_RUN.sub("", title).strip()
    if len(stripped) < 2:
        # 兜底：整条标题都是引流 tag，不剔除，仅去掉 # 号保留全部词语
        stripped = title
    cleaned = _HASHTAG_TOKEN.sub(r"\1", stripped)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or title.strip()


def approx_token_count(text: str) -> int:
    """
    估算文本的 Token 数量

    中文按字计，英文按 4 字符 ≈ 1 Token 估算。
    用于切块时控制块大小。

    :param text: 输入文本
    :return: 近似 Token 数量
    """
    if not text:
        return 0
    cjk = len(re.findall(r"[一-鿿]", text))
    latin = len(text) - cjk
    return cjk + max(1, latin // 4)


def build_fixed_chunks(
    text: str, chunk_size: int | None = None, overlap: int | None = None
) -> list[str]:
    """
    固定大小文本切块

    将长文本按固定字符数切分为多个块，
    相邻块之间有 overlapping 以保持上下文连贯性。

    :param text: 输入文本
    :param chunk_size: 每块大小（字符数），默认从配置读取
    :param overlap: 重叠大小（字符数），默认从配置读取
    :return: 文本块列表
    """
    if not text.strip():
        return []

    size = chunk_size or settings.chunk_size
    overlap_size = overlap or settings.chunk_overlap

    chunks: list[str] = []
    start = 0
    text_len = len(text)

    while start < text_len:
        end = min(start + size, text_len)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start += size - overlap_size

    return chunks


def build_chunks_from_segments(
    segments: list, max_tokens: int = 220
) -> list[ChunkResult]:
    """
    基于 ASR 时间戳片段进行语义切块

    将连续的语音片段按 Token 上限聚合为文本块，
    每个块保留原始音频时间范围。

    :param segments: ASR 转写片段列表（RawSegment）
    :param max_tokens: 每块最大 Token 数
    :return: 带时间信息的文本块列表
    """
    chunks: list[ChunkResult] = []
    if not segments:
        return chunks

    bucket_text: list[str] = []
    bucket_start = segments[0].start_ms
    bucket_end = segments[0].end_ms
    bucket_lang = segments[0].lang

    for seg in segments:
        cleaned = clean_text(seg.text)
        if not cleaned:
            continue

        candidate = (
            " ".join(bucket_text + [cleaned])
            if bucket_text
            else cleaned
        )
        token_count = approx_token_count(candidate)

        if bucket_text and token_count > max_tokens:
            current_text = " ".join(bucket_text)
            chunks.append(
                ChunkResult(
                    text=current_text,
                    token_count=approx_token_count(current_text),
                    start_ms=bucket_start,
                    end_ms=bucket_end,
                    lang=bucket_lang,
                )
            )
            bucket_text = [cleaned]
            bucket_start = seg.start_ms
            bucket_end = seg.end_ms
            bucket_lang = seg.lang
        else:
            bucket_text.append(cleaned)
            bucket_end = seg.end_ms

    if bucket_text:
        final_text = " ".join(bucket_text)
        chunks.append(
            ChunkResult(
                text=final_text,
                token_count=approx_token_count(final_text),
                start_ms=bucket_start,
                end_ms=bucket_end,
                lang=bucket_lang,
            )
        )

    return chunks
