"""版本号单一来源：仓库根 `version.txt`（由 release-please 维护）。

后端 API 与前端徽标都运行时读它，避免各处硬编码漏改。
"""
from __future__ import annotations

from pathlib import Path

_FALLBACK = "0.0.0"


def get_version() -> str:
    for candidate in (
        Path(__file__).resolve().parents[2] / "version.txt",   # 仓库根 <repo>/version.txt
        Path(__file__).resolve().parents[1] / "version.txt",   # backend/ 内兜底
    ):
        try:
            text = candidate.read_text(encoding="utf-8").strip()
            if text:
                return text
        except OSError:
            continue
    return _FALLBACK


__version__ = get_version()
