"""
进程级模型调用准入闸门

入库流水线（≤3 个工作线程）与任意数量并发的 /ask、/ask/stream 请求都会
调用 DashScope/LLM 供应商（chat、stream_chat、embedding、vision），此前
没有任何东西给这些调用设进程级并发上限——两边叠加起来对上游供应商的并发
请求数完全没有封顶。这里用一个共享的有界信号量统一限制，超时拿不到名额
就抛出一个可辨识的异常，调用方按各自既有的降级/重试约定处理，不引入新
的错误处理约定。
"""
from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterator

from app.core.config import settings


class ModelCallAdmissionTimeout(RuntimeError):
    """
    无法在配置时限内获得模型调用名额时抛出。

    这不是网络/鉴权错误——调用方不应该把它当瞬态错误盲目重试；各调用点
    按自己既有的降级/失败约定处理即可（详见各服务模块）。
    """

    def __init__(self, call_kind: str, waited_seconds: float):
        self.call_kind = call_kind
        self.waited_seconds = waited_seconds
        super().__init__(
            f"模型调用排队超时（{call_kind}），已等待 {waited_seconds:.1f}s，请稍后重试"
        )


_gate = threading.BoundedSemaphore(settings.model_call_max_concurrency)


@contextlib.contextmanager
def acquire_model_call_slot(call_kind: str) -> Iterator[None]:
    """
    LLM chat/stream、Embedding、Vision 四类调用共享一个进程级闸门。

    :param call_kind: 调用类别标签（如 "llm_chat"/"llm_stream"/"embedding"/
        "vision"），只用于日志与异常信息，不影响闸门本身（闸门是单个共享
        信号量，不是按类别分开限流）。
    """
    timeout = settings.model_call_admission_timeout_seconds
    if not _gate.acquire(timeout=timeout):
        raise ModelCallAdmissionTimeout(call_kind, timeout)
    try:
        yield
    finally:
        _gate.release()
