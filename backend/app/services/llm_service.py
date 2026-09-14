"""
LLM 与 Embedding 客户端模块

封装两个上游服务：
1. DeepSeek（OpenAI 兼容协议）—— LLM 对话 + 流式输出
2. DashScope —— 文本向量化（Embedding）
"""
from __future__ import annotations

import json
import logging
import math
import time
from typing import Iterable, Sequence

import dashscope
import httpx
from dashscope import TextEmbedding
from openai import OpenAI
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed
from requests.exceptions import RequestException

from app.core.config import settings
from app.core.model_gate import acquire_model_call_slot

logger = logging.getLogger(__name__)

ANTHROPIC_API_VERSION = "2023-06-01"

# 固定向量维度：与既有 Chroma collection（akasha_*, dim=1024）对齐。
# qwen3.7-text-embedding / text-embedding-v4 / v3 均支持 dimension 参数。
# 更换 EMBEDDING_MODEL 或修改此值后，必须清空知识库重建向量索引。
EMBEDDING_DIMENSION = 1024


class LLMClient:
    """
    LLM 客户端

    支持两种协议的对话供应商：OpenAI 兼容协议（DeepSeek 及绝大多数第三方网关）
    与 Anthropic 兼容协议（Claude 及其网关）。协议、地址、Key、模型都来自设置面板
    保存的"当前激活供应商"（见 `app.services.settings_store`）；没有保存过供应商时，
    回退到 `.env` 里的单一 DeepSeek 配置（`llm_base_url` / `llm_model` /
    `deepseek_api_key`），保持既有 `.env`-only 用法不变。

    每次调用都重新解析当前激活的供应商，而不是在构造时固定下来——这样设置面板里
    切换/编辑供应商能立刻生效，不需要重启后端。
    """

    def _resolve_provider(self) -> tuple[str, str, str, str]:
        """返回 (protocol, base_url, api_key, model_id)。"""
        from app.services.settings_store import get_active_chat_provider

        provider = get_active_chat_provider()
        if provider is not None:
            return provider.protocol, provider.base_url, provider.api_key, provider.model_id
        api_key = settings.deepseek_api_key.strip() if settings.deepseek_api_key else ""
        return "openai", settings.llm_base_url, api_key, settings.llm_model

    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.6,
        max_tokens: int = 2048,
        timeout: float = 60.0,
    ) -> str:
        """
        发送非流式对话请求

        :param system_prompt: 系统提示词
        :param user_prompt: 用户消息
        :param temperature: 温度参数（0-2）
        :param max_tokens: 最大输出 Token 数
        :param timeout: 超时时间（秒）
        :return: LLM 生成的完整回复
        """
        # 闸门包在被 @retry 装饰的方法外面一层：_chat_with_retry 的重试
        # 装饰器没有白名单也没有 reraise=True，如果闸门在里面，
        # ModelCallAdmissionTimeout 会被盲目重试 3 次、最后包成不可辨识的
        # tenacity.RetryError。
        # TODO(后续批次): 视情况引入"排队等待 + SDK 超时"的统一剩余 deadline 传播。
        with acquire_model_call_slot("llm_chat"):
            return self._chat_with_retry(system_prompt, user_prompt, temperature, max_tokens, timeout)

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(1))
    def _chat_with_retry(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        timeout: float,
    ) -> str:
        protocol, base_url, api_key, model = self._resolve_provider()
        started = time.perf_counter()
        if protocol == "anthropic":
            text = _anthropic_chat(base_url, api_key, model, system_prompt, user_prompt, temperature, max_tokens, timeout)
        else:
            text = _openai_chat(base_url, api_key, model, system_prompt, user_prompt, temperature, max_tokens, timeout)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info("LLM chat 完成 (%sms, protocol=%s, model=%s)", elapsed_ms, protocol, model)
        return text

    def stream_chat(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.6,
        max_tokens: int = 2048,
        timeout: float = 60.0,
    ) -> Iterable[str]:
        """
        发送流式对话请求

        逐 token 返回生成内容，适合 SSE 推送给前端。

        :param system_prompt: 系统提示词
        :param user_prompt: 用户消息
        :param temperature: 温度参数
        :param max_tokens: 最大输出 Token 数
        :param timeout: 超时时间（秒）
        :yield: 每次生成的一小段文本
        """
        protocol, base_url, api_key, model = self._resolve_provider()
        started = time.perf_counter()
        if protocol == "anthropic":
            stream_fn = _anthropic_stream_chat
        else:
            stream_fn = _openai_stream_chat
        # 闸门包在整个生成器体外面，让名额横跨整个 SSE 生成周期——不是只
        # 包住建立连接那一下。生成器的 with 块在正常耗尽、抛异常、或被
        # 外部 .close() 时都会执行 __exit__，这是后续取消传播能正确释放
        # 名额的关键。
        with acquire_model_call_slot("llm_stream"):
            yield from stream_fn(base_url, api_key, model, system_prompt, user_prompt, temperature, max_tokens, timeout)

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info("LLM stream 完成 (%sms, protocol=%s, model=%s)", elapsed_ms, protocol, model)


def _openai_chat(base_url: str, api_key: str, model: str, system_prompt: str, user_prompt: str,
                  temperature: float, max_tokens: int, timeout: float) -> str:
    client = OpenAI(api_key=api_key or "sk-placeholder", base_url=base_url)
    response = client.chat.completions.create(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return response.choices[0].message.content or ""


def _openai_stream_chat(base_url: str, api_key: str, model: str, system_prompt: str, user_prompt: str,
                         temperature: float, max_tokens: int, timeout: float) -> Iterable[str]:
    client = OpenAI(api_key=api_key or "sk-placeholder", base_url=base_url)
    stream = client.chat.completions.create(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        stream=True,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    for chunk in stream:
        delta = chunk.choices[0].delta
        if delta and delta.content:
            yield delta.content


def _anthropic_headers(api_key: str) -> dict:
    return {
        "x-api-key": api_key or "placeholder",
        "anthropic-version": ANTHROPIC_API_VERSION,
        "content-type": "application/json",
    }


def _anthropic_chat(base_url: str, api_key: str, model: str, system_prompt: str, user_prompt: str,
                     temperature: float, max_tokens: int, timeout: float) -> str:
    resp = httpx.post(
        f"{base_url.rstrip('/')}/v1/messages",
        headers=_anthropic_headers(api_key),
        json={
            "model": model,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    parts = data.get("content") or []
    return "".join(part.get("text", "") for part in parts if isinstance(part, dict) and part.get("type") == "text")


def _anthropic_stream_chat(base_url: str, api_key: str, model: str, system_prompt: str, user_prompt: str,
                            temperature: float, max_tokens: int, timeout: float) -> Iterable[str]:
    with httpx.stream(
        "POST",
        f"{base_url.rstrip('/')}/v1/messages",
        headers=_anthropic_headers(api_key),
        json={
            "model": model,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        },
        timeout=timeout,
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                event = json.loads(payload)
            except ValueError:
                continue
            if event.get("type") != "content_block_delta":
                continue
            delta = event.get("delta") or {}
            text = delta.get("text")
            if text:
                yield text


class _TransientEmbeddingError(RuntimeError):
    pass


class EmbeddingClient:
    """
    Embedding 客户端

    使用 DashScope TextEmbedding API。
    """

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """
        将文本列表转换为向量

        :param texts: 待向量化的文本列表
        :return: 对应的向量列表，每个向量为 float 列表
        :raises RuntimeError: API Key 未配置或调用失败
        """
        if not texts:
            return []

        texts = list(texts)
        started = time.perf_counter()

        embeddings = []
        # text-embedding-v4 accepts at most ten texts per synchronous request.
        for offset in range(0, len(texts), 10):
            embeddings.extend(self._embed_batch(texts[offset:offset + 10]))
        if len({len(vector) for vector in embeddings}) != 1:
            raise RuntimeError("Embedding 返回的向量维度不一致")

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "Embedding 完成: %d texts, %dms, model=%s",
            len(texts),
            elapsed_ms,
            settings.embedding_model,
        )

        return embeddings

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(1), reraise=True,
           retry=retry_if_exception_type((_TransientEmbeddingError, RequestException)))
    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        from app.core.config import require_dashscope_key
        key = require_dashscope_key()
        # ModelCallAdmissionTimeout 不在下面的 retry 白名单里，会立即
        # 原样传播，不会被这个方法自己的 @retry 盲目重试。
        # TODO(后续批次): 视情况引入"排队等待 + SDK 超时"的统一剩余 deadline 传播。
        with acquire_model_call_slot("embedding"):
            resp = TextEmbedding.call(
                model=settings.embedding_model, input=texts, api_key=key,
                dimension=EMBEDDING_DIMENSION, request_timeout=30,
            )
        if resp.status_code != 200:
            error_type = (_TransientEmbeddingError if resp.status_code in (408, 429, 500, 502, 503, 504)
                          else RuntimeError)
            message = str(resp.message).replace(key, "[redacted]")[:500]
            raise error_type(f"Embedding 调用失败: status={resp.status_code}, message={message}")
        output = resp.output
        items = output.get("embeddings") if isinstance(output, dict) else None
        if not isinstance(items, list) or len(items) != len(texts):
            raise RuntimeError("Embedding 返回数量与输入文本不匹配")
        ordered = [None] * len(texts)
        for item in items:
            index = item.get("text_index") if isinstance(item, dict) else None
            vector = item.get("embedding") if isinstance(item, dict) else None
            if (type(index) is not int or not 0 <= index < len(texts) or ordered[index] is not None
                    or not isinstance(vector, list) or not vector
                    or any(type(value) not in (int, float) or not math.isfinite(value) for value in vector)):
                raise RuntimeError("Embedding 返回无效索引或向量，已拒绝错配正文")
            ordered[index] = vector
        return ordered

    def embed_text(self, text: str) -> list[float]:
        """
        将单条文本转换为向量

        :param text: 待向量化的文本
        :return: 向量
        """
        return self.embed_texts([text])[0]


# 全局单例
llm_client = LLMClient()
embedding_client = EmbeddingClient()
