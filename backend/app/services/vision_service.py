"""
多模态视觉服务模块

针对抖音图文作品（images_base）或无音频作品：
1. 抓取/提取图文作品包含的全部高清图片
2. 调用 DashScope Qwen-VL 多模态视觉大模型提取图文/图表中的全部知识文字
3. 生成详尽的原始转写文本，供切块与向量化检索
"""
from __future__ import annotations

import base64
import json
import logging
import re
import urllib.parse
from pathlib import Path
from typing import List, Optional

import dashscope
import requests
from dashscope import MultiModalConversation

from app.core.config import settings
from app.core.external_urls import safe_platform_image_url

logger = logging.getLogger(__name__)
_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_MAX_IMAGE_REDIRECTS = 3


class VisionService:
    """视觉文字识别与提取服务"""

    def __init__(self) -> None:
        self.user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )

    def _get_cookies(self) -> dict:
        """读取 Playwright 登录态中的 Cookie"""
        candidates = [
            Path(settings.playwright_user_data_dir) / "state.json",
            Path.cwd() / settings.playwright_user_data_dir / "state.json",
            Path(__file__).resolve().parent.parent / "storage" / "playwright_user_data" / "state.json",
        ]
        for p in candidates:
            if p.exists():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        return {c["name"]: c["value"] for c in data.get("cookies", []) if c.get("name")}
                except Exception:
                    pass
        return {}

    def _download_trusted_image(self, image_url: str, headers: dict[str, str]) -> bytes | None:
        """Download one allowlisted image without following an unchecked redirect."""
        current_url = safe_platform_image_url("douyin", image_url)
        for _ in range(_MAX_IMAGE_REDIRECTS + 1):
            if not current_url:
                return None
            try:
                response = requests.get(
                    current_url,
                    headers=headers,
                    timeout=(5, 20),
                    allow_redirects=False,
                    stream=True,
                )
            except requests.RequestException:
                return None
            try:
                if response.is_redirect or response.is_permanent_redirect:
                    redirect_url = urllib.parse.urljoin(current_url, response.headers.get("Location", ""))
                    current_url = safe_platform_image_url(
                        "douyin", redirect_url
                    )
                    continue
                if response.status_code != 200:
                    return None
                try:
                    declared_size = int(response.headers.get("Content-Length", "0"))
                except ValueError:
                    return None
                if declared_size > _MAX_IMAGE_BYTES:
                    return None
                chunks: list[bytes] = []
                received = 0
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    received += len(chunk)
                    if received > _MAX_IMAGE_BYTES:
                        return None
                    chunks.append(chunk)
                return b"".join(chunks)
            finally:
                response.close()
        return None

    def fetch_note_image_urls(self, platform_item_id: str, fallback_cover: Optional[str] = None) -> List[str]:
        """
        获取图文笔记包含的全部图片 URL

        :param platform_item_id: 抖音图文 ID
        :param fallback_cover: 封面备选 URL
        :return: 图片 URL 列表
        """
        cookies = self._get_cookies()
        headers = {
            "User-Agent": self.user_agent,
            "Referer": "https://www.douyin.com/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        }

        urls: List[str] = []

        candidate_urls = [
            f"https://www.douyin.com/note/{platform_item_id}",
            f"https://www.douyin.com/video/{platform_item_id}",
        ]

        for target_url in candidate_urls:
            try:
                resp = requests.get(target_url, headers=headers, cookies=cookies, timeout=10)
                if resp.status_code == 200:
                    text_content = resp.text

                    # 1. 尝试从 _RENDER_DATA 解析
                    if "_RENDER_DATA" in text_content:
                        m = re.search(r'<script id="_RENDER_DATA"[^>]*>(.*?)</script>', text_content)
                        if m:
                            try:
                                raw = urllib.parse.unquote(m.group(1))
                                data = json.loads(raw)

                                def find_image_urls(obj):
                                    found = []
                                    if isinstance(obj, dict):
                                        if "url_list" in obj and isinstance(obj["url_list"], list):
                                            for u in obj["url_list"]:
                                                safe_url = safe_platform_image_url("douyin", u)
                                                if safe_url:
                                                    found.append(safe_url)
                                                    break
                                        for v in obj.values():
                                            found.extend(find_image_urls(v))
                                    elif isinstance(obj, list):
                                        for item in obj:
                                            found.extend(find_image_urls(item))
                                    return found

                                extracted = find_image_urls(data)
                                for u in extracted:
                                    cleaned = u.split("?")[0]
                                    if cleaned not in [x.split("?")[0] for x in urls]:
                                        urls.append(u)
                            except Exception:
                                pass

                    # 2. 仅当结构化解析一无所获时，才从全 HTML 扫描 CDN 图片地址（噪音多，需强去噪）
                    if not urls:
                        img_matches = re.findall(r'https://[a-zA-Z0-9\-\.]+(?:douyinpic|byteimg|tos-cn)[^\s"\'<>]+', text_content)
                        seen = set()
                        invalid_exts = (".js", ".css", ".json", ".map", ".wasm", ".html", ".xml", ".txt", ".mp3", ".mp4")
                        # 噪音特征：头像 / 互动图标 / UI 静态资源 / 关联推荐封面 / 小缩略图。
                        # 注意：~c5 / _c5 是图文笔记高清正图的 CDN 模板标记，绝不能过滤！
                        noise_markers = (
                            "avatar", "chat_days", "static-resource", "wallpapers",
                            "relation", "recommend", "web-cover", "100x100", "50x50",
                            "aweme/v1", "sdk", "bdms",
                        )
                        for u in img_matches:
                            low = u.lower()
                            if any(m in low for m in noise_markers):
                                continue
                            cleaned = u.split("?")[0].lower()
                            if any(cleaned.endswith(ext) for ext in invalid_exts):
                                continue
                            # 去掉尺寸后缀（~c5_1080x1080 等）后去重，避免同图多分辨率重复
                            dedup_key = re.sub(r"[~_]c5[^/]*$", "", cleaned)
                            dedup_key = re.sub(r"_\d{2,4}x\d{2,4}", "", dedup_key)
                            safe_url = safe_platform_image_url("douyin", u)
                            if safe_url and dedup_key not in seen:
                                seen.add(dedup_key)
                                urls.append(safe_url)
                        urls = urls[:24]

                    if urls:
                        break
            except Exception as e:
                logger.warning("请求页面解析失败 [%s -> %s]: %s", platform_item_id, target_url, e)

        if not urls:
            safe_fallback = safe_platform_image_url("douyin", fallback_cover)
            if safe_fallback:
                urls.append(safe_fallback)

        logger.info("图文笔记 [%s] 提取到 %d 张候选图片", platform_item_id, len(urls))
        return urls

    def extract_text_from_images(self, image_urls: List[str], title: str = "") -> str:
        """
        调用 DashScope Qwen-VL 多模态视觉模型提取图中文本与图表内容

        :param image_urls: 图片 URL 列表
        :param title: 作品标题
        :return: 提取整理后的完整文字
        """
        if not image_urls:
            return ""

        from app.core.config import require_dashscope_key
        try:
            dashscope.api_key = require_dashscope_key()
        except ValueError as exc:
            logger.warning("视觉多模态大模型不可用: %s", exc)
            return ""

        # 多下候选、按魔数校验后取前 8 张真图喂 Qwen-VL（HTML 扫描来的候选常混入非图资源）
        MAX_VISION_IMAGES = 8
        candidate_urls = [
            safe_url
            for url in image_urls[:16]
            if (safe_url := safe_platform_image_url("douyin", url))
        ]
        extracted_sections = []

        headers = {
            "User-Agent": self.user_agent,
            "Referer": "https://www.douyin.com/",
        }

        # 1. 线程池并发下载候选图片
        from concurrent.futures import ThreadPoolExecutor

        def fetch_single_image(item: tuple[int, str]) -> tuple[int, Optional[str]]:
            idx, img_url = item
            content = None
            for attempt in range(2):
                content = self._download_trusted_image(img_url, headers)
                if content is not None:
                    break
            if content is None:
                logger.warning("下载图片失败 [%d]", idx)
                return idx, None
            try:
                if len(content) < 800:
                    return idx, None
                # 严格基于二进制魔数校验合法图片格式，杜绝将 JS/HTML 传入 DashScope
                mime = None
                if content.startswith(b"\xff\xd8\xff"):
                    mime = "image/jpeg"
                elif content.startswith(b"\x89PNG\r\n\x1a\n") or content.startswith(b"\x89PNG"):
                    mime = "image/png"
                elif content.startswith(b"RIFF") and b"WEBP" in content[:16]:
                    mime = "image/webp"
                elif content.startswith(b"GIF87a") or content.startswith(b"GIF89a"):
                    mime = "image/gif"
                elif content.startswith(b"BM"):
                    mime = "image/bmp"

                if not mime:
                    logger.debug("URL [%s] 响应非合法图片二进制数据，跳过", img_url[:60])
                    return idx, None

                b64_data = base64.b64encode(content).decode("utf-8")
                return idx, f"data:{mime};base64,{b64_data}"
            except Exception as e:
                logger.warning("下载图片失败 [%d]: %s", idx, e)
                return idx, None

        items = list(enumerate(candidate_urls, 1))
        with ThreadPoolExecutor(max_workers=min(6, len(items))) as pool:
            fetched = list(pool.map(fetch_single_image, items))
        # 按候选顺序取通过魔数校验的前 8 张真图
        downloaded_images = [(i, uri) for i, uri in fetched if uri][:MAX_VISION_IMAGES]

        # 2. 依次提取图中文本与图表内容
        prompt = (
            "你是一位专业的笔记整理与知识提取助手。请仔细查看这张图片，"
            "完整、准确地提取其中的所有文字、标题、列表、要点、公式或图表数据。"
            "保持原文结构，不要编造，如果包含表格请用 Markdown 表格呈现。"
        )

        for idx, data_uri in downloaded_images:
            if not data_uri:
                continue
            try:

                messages = [
                    {
                        "role": "user",
                        "content": [
                            {"image": data_uri},
                            {"text": prompt},
                        ],
                    }
                ]

                response = MultiModalConversation.call(
                    model=settings.vision_model,
                    messages=messages,
                )

                if response.status_code == 200 and response.output and response.output.choices:
                    content = response.output.choices[0].message.content
                    if isinstance(content, list):
                        # 兼容 content 结构
                        text_parts = [c.get("text", "") for c in content if isinstance(c, dict) and "text" in c]
                        text_res = "\n".join(text_parts).strip()
                    else:
                        text_res = str(content).strip()

                    if text_res:
                        extracted_sections.append(f"### [图片 {idx} 内容提取]\n{text_res}")
                else:
                    logger.warning("Qwen-VL 调用异常: %s", response.message if hasattr(response, "message") else response)

            except Exception as e:
                logger.warning("提取图片 [%d] 文字失败: %s", idx, e)

        if not extracted_sections:
            return ""

        full_extracted = "\n\n".join(extracted_sections)
        logger.info("图文作品视觉提取成功: 共提取 %d 张图片，文本字符数: %d", len(extracted_sections), len(full_extracted))
        return full_extracted


vision_service = VisionService()
