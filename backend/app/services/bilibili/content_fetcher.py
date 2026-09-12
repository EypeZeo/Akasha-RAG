"""
Bilibili 视频正文提取器

策略：
1. 字幕优先 (Subtitle Priority)：
   优先探测官方字幕与 B 站 AI 自动生成的字幕。
   若字幕正文有效 (>= 50 字符)，直接采用，实现零成本、秒级入库。
2. DASH 音频降级 (Audio ASR Fallback)：
   若无有效字幕，探测最低码率音频流 (<= 64kbps)，下载到音频缓存后使用 ASR 转写。
3. 严格拒绝非实质性兜底：
   若字幕与 ASR 提取的实质正文均低于 50 字符，坚决报错失败，拒绝仅标题/简介入库污染向量检索。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from app.core.config import settings
from app.services.asr_service import asr_service
from app.services.bilibili.client import bilibili_client
from app.services.media_service import audio_cache_lease, transcode_audio_to_mp3
from app.services.text_processing import clean_title_for_index

logger = logging.getLogger(__name__)

# 实质正文最小有效字符阈值
SUBSTANTIVE_TEXT_MIN_CHARS = 50


class BilibiliContentFetcher:
    """B 站视频正文提取服务 (字幕优先 + DASH 音频 ASR)"""

    def __init__(self) -> None:
        self._client = bilibili_client
        self._cache_dir = Path(settings.bilibili_audio_cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    async def fetch_video_pages(self, bvid: str) -> list[dict]:
        """
        获取视频的所有分P (pages) 列表

        :return: [{'cid': int, 'page': int, 'part': str, 'duration': int}]
        """
        info = await self._client.get_video_info(bvid)
        pages = info.get("pages") or []
        result = []
        for idx, p in enumerate(pages, start=1):
            cid = p.get("cid")
            if cid:
                result.append({
                    "cid": int(cid),
                    "page": int(p.get("page") or idx),
                    "part": str(p.get("part") or f"P{idx}"),
                    "duration": int(p.get("duration") or 0),
                })
        if not result and info.get("cid"):
            result.append({
                "cid": int(info["cid"]),
                "page": 1,
                "part": "P1",
                "duration": int(info.get("duration") or 0),
            })
        return result

    async def fetch_transcript(
        self,
        bvid: str,
        cid: int,
        title: str = "",
        part_title: str = "",
    ) -> str:
        """
        提取单分P视频的实质正文 (字幕优先 -> DASH 音频 ASR)

        :param bvid: 视频 BV 号
        :param cid: 分P cid
        :param title: 视频主标题
        :param part_title: 分P标题 (若有多P)
        :return: 提取到的实质性正文
        :raises RuntimeError: 当无法提取到大于阈值的实质正文时抛出
        """
        clean_main_title = clean_title_for_index(title)
        if cid <= 0:
            pages = await self.fetch_video_pages(bvid)
            if not pages:
                raise RuntimeError(f"未能解析到视频的分P信息 [{bvid}]")
            cid = pages[0]["cid"]
            if not part_title and pages[0].get("part"):
                part_title = pages[0]["part"]

        full_title = clean_main_title
        if part_title and part_title != title and part_title != "P1":
            full_title = f"{clean_main_title} - {clean_title_for_index(part_title)}"

        # --------------------------------------------------------------
        # 步骤 1: 尝试提取字幕 (官方人工字幕或 AI 字幕)
        # --------------------------------------------------------------
        try:
            player_info = await self._client.get_player_info(bvid, cid)
            subtitle_info = player_info.get("subtitle") or {}
            subtitles = subtitle_info.get("subtitles") or []
            if subtitles:
                # 优先级排序: 中文优先 (zh-CN, zh-Hans, ai-zh)
                def _sub_priority(s: dict) -> int:
                    lan = s.get("lan", "").lower()
                    if lan in ("zh-cn", "zh-hans"):
                        return 0
                    if "ai-zh" in lan or "zh" in lan:
                        return 1
                    return 2

                sorted_subs = sorted(subtitles, key=_sub_priority)
                target_sub = sorted_subs[0]
                sub_url = target_sub.get("subtitle_url")
                if sub_url:
                    logger.info(
                        "[%s cid=%s] 检测到字幕 (%s)，正在下载...",
                        bvid, cid, target_sub.get("lan_doc", "中文"),
                    )
                    sub_text = await self._client.download_subtitle(sub_url)
                    if sub_text and len(sub_text.strip()) >= SUBSTANTIVE_TEXT_MIN_CHARS:
                        logger.info(
                            "[%s cid=%s] 成功提取字幕 (%d 字符)，无需 ASR 转写",
                            bvid, cid, len(sub_text),
                        )
                        return f"【视频字幕】{full_title}\n\n{sub_text.strip()}"
                    logger.info("[%s cid=%s] 字幕文本过短 (%d 字符)，尝试音频 ASR", bvid, cid, len(sub_text or ""))
        except Exception as exc:
            logger.warning("[%s cid=%s] 字幕探测/下载失败: %s", bvid, cid, exc)

        # --------------------------------------------------------------
        # 步骤 2: DASH 音频下载 + 本地 ASR 转写
        # --------------------------------------------------------------
        logger.info("[%s cid=%s] 尝试获取 DASH 音频流进行 ASR 转写...", bvid, cid)
        audio_url = await self._client.get_audio_url(bvid, cid)
        if not audio_url:
            raise RuntimeError(f"未能获取到 B 站音频流直链 [{bvid}]")

        lease_key = f"bili_{bvid}_{cid}"
        raw_audio_file = self._cache_dir / f"{lease_key}.m4a.part"
        audio_file = self._cache_dir / f"{lease_key}.mp3"

        with audio_cache_lease(lease_key):
            try:
                ok = await self._client.download_audio_to_file(audio_url, raw_audio_file)
                if not ok or not raw_audio_file.exists():
                    raise RuntimeError(f"B 站音频流下载失败或文件损坏 [{bvid}]")
                # Dash audio commonly arrives as M4A.  DashScope ASR accepts
                # only MP3/WAV in this pipeline, so never pass the raw DASH
                # container through directly.
                transcode_audio_to_mp3(raw_audio_file, audio_file)

                logger.info(
                    "[%s cid=%s] 音频下载成功 (%.2f MB)，开始 ASR 语音识别...",
                    bvid, cid, audio_file.stat().st_size / (1024 * 1024),
                )
                asr_text = asr_service.transcribe_to_text(audio_file)
                if not asr_text or len(asr_text.strip()) < SUBSTANTIVE_TEXT_MIN_CHARS:
                    raise RuntimeError(
                        f"ASR 未能识别到实质正文 (提取字符数 < {SUBSTANTIVE_TEXT_MIN_CHARS})"
                    )

                return f"【视频语音转写】{full_title}\n\n{asr_text.strip()}"
            finally:
                for path in (raw_audio_file, audio_file):
                    if not path.exists():
                        continue
                    try:
                        path.unlink(missing_ok=True)
                    except Exception as exc:
                        logger.debug("清理临时音频文件异常: %s", exc)


# 全局提取器单例
bilibili_content_fetcher = BilibiliContentFetcher()
