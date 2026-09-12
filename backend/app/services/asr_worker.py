"""ASR 隔离工作进程：stdin 接收请求，stdout 只返回一份 JSON。

不加载应用配置或数据库；父进程负责截止时间、终止和进程回收。
"""
from __future__ import annotations

from contextlib import redirect_stdout
import json
import logging
import sys

from dashscope.audio.asr import Recognition


def recognize(request: dict) -> list[dict]:
    recognition = Recognition(
        model=request["model"],
        format=request["format"],
        sample_rate=16000,
        callback=None,
        api_key=request["api_key"],
        request_timeout=request["request_timeout"],
    )
    result = recognition.call(request["audio_path"])
    if result is None:
        raise RuntimeError("服务未返回识别结果")
    if result.status_code != 200:
        raise RuntimeError(
            f"status={result.status_code}, code={result.code}, "
            f"request_id={result.request_id}, message={result.message}"
        )
    sentences = result.get_sentence()
    if sentences is None:
        return []
    if isinstance(sentences, dict):
        sentences = [sentences]
    if not isinstance(sentences, list):
        raise ValueError("ASR sentence 字段类型无效")

    segments: list[dict] = []
    for sentence in sentences:
        if not isinstance(sentence, dict):
            raise ValueError("ASR 句子格式无效")
        text = sentence.get("text")
        if text is None or text == "":
            continue
        if not isinstance(text, str):
            raise ValueError("ASR 句子正文类型无效")
        text = text.strip()
        if not text:
            continue
        # 未完成的流式中间结果不具备可溯源的结束时间，不能入库。
        start = sentence.get("begin_time")
        end = sentence.get("end_time")
        if (
            type(start) is not int
            or type(end) is not int
            or start < 0
            or end < start
        ):
            raise ValueError("ASR 句子时间戳缺失或无效，拒绝生成错误溯源")
        segments.append({"text": text, "start_ms": start, "end_ms": end, "lang": "zh"})
    return segments


def main() -> None:
    # SDK 日志/输出可能含完整响应；协议之外的输出不进入父进程日志。
    logging.disable(logging.CRITICAL)
    request: dict = {}
    try:
        request = json.load(sys.stdin)
        with redirect_stdout(sys.stderr):
            segments = recognize(request)
        response = {"segments": segments}
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        api_key = request.get("api_key") if isinstance(request, dict) else None
        if isinstance(api_key, str) and api_key:
            message = message.replace(api_key, "[redacted]")
        response = {"error": message[:1000]}
    print(json.dumps(response, ensure_ascii=False))


if __name__ == "__main__":
    main()
