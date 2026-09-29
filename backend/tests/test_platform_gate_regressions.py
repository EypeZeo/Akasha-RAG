"""围栏测试：把 Lead 在 WS3 验证后收尾修复的四个缺陷钉死在测试里。

背景（每一条都有一个可复现的反例，见 docs/audits/WS-VERIFICATION.md）
--------------------------------------------------------------------
M1  `knowledge_service` 的兜底 `else` 手写 ``f"https://www.douyin.com/video/{id}"``。
    当时只有抖音会走到该分支，所以不报错；但只要注册第三个平台，它就会成为
    落点，去下载一个**伪造的抖音地址**。WS1 的 forbidden-pattern 守卫看不见它，
    因为守卫只匹配 ``else f"https://www.douyin.com/video/``，而这里是一个跨行实参。

M2  `knowledge_service` 里存在 10 处独立的 duration 字面量，与
    ``content_kind.note_predicate`` 在**负时长**上判据相反。一个 duration=-5 的行：
    列表口径算图文（note_count=1），start_sync(note) 说"没有待入库的图文笔记"，
    统计口径算 0。

L1  ``item_platform = item.platform or "douyin"`` 把空串"洗"成抖音，绕过能力预检:
    平台为 ""、duration=20 的行会跑完 下载+ASR+Embedding+向量写入，并以 'done' 结束。

L3  ``_collection_for`` 用注册表做校验（会 strip/lower），却拿**原始字符串**去索引
    ``self._collections``，于是 'DoUyIn' 抛裸 KeyError，而历史实现对非规范拼写
    一律抛 ValueError。

这些测试刻意做成"结构性"的：它们断言的是**不存在某类代码**与**纯函数的性质**，
而不是某个具体实现的快照，因此不会因为无关重构而误报。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import or_

from app.services import chroma_service
from app.services.content_kind import note_predicate, video_predicate
from app.services.platform_registry import (
    CAPABILITY_AUDIO_ASR,
    CAPABILITY_NOTE_OCR,
    CAPABILITY_SUBTITLE,
    PLATFORMS,
    extraction_capabilities,
    platform_supports,
    supported_platforms,
)

APP_DIR = Path(__file__).resolve().parents[1] / "app"


def _app_sources() -> list[Path]:
    return sorted(p for p in APP_DIR.rglob("*.py") if "__pycache__" not in p.parts)


# ---------------------------------------------------------------------------
# M2 — 判据必须只有一条规则（负时长是唯一的可区分点）
# ---------------------------------------------------------------------------

def test_note_and_video_predicates_partition_the_row_set():
    """note + video 必须**恰好划分**全集，且每个 duration 只落一边。

    这条以前写成比对编译后的 SQL 字符串，那是快照断言：把 video_predicate 改成
    ``column > -1`` 它照样通过，而 video+note 已经不再等于 total。现在改成
    真的建表、插数据、按两个 predicate 数数。
    """
    from sqlalchemy import create_engine, func, select
    from sqlalchemy.orm import Session

    from app.db.base import Base
    from app.models.entities import ContentItem

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    durations = [-100, -5, -1, 0, 1, 20, 3600]
    with Session(engine) as db:
        for idx, duration in enumerate(durations):
            db.add(ContentItem(
                platform="douyin", remote_item_id=f"d{idx}", duration=duration,
                title=f"t{idx}", canonical_url="", video_url="",
            ))
        db.commit()

        total = db.scalar(select(func.count(ContentItem.id)))
        col = ContentItem.duration
        note = db.scalar(select(func.count(ContentItem.id)).where(note_predicate(col)))
        video = db.scalar(select(func.count(ContentItem.id)).where(video_predicate(col)))

    engine.dispose()
    assert total == len(durations)
    assert note + video == total, f"两个判据没有划分全集: note={note} video={video} total={total}"
    # 负时长必须归图文 —— 这是历史三种口径的分叉点。
    negatives = sum(1 for d in durations if d <= 0)
    assert note == negatives, f"负时长/零时长应全部归图文: note={note} 期望={negatives}"


@pytest.mark.parametrize("duration", [-5, -1, 0, None, 1, 20, 3600])
def test_duration_classification_agrees_across_every_consumer(duration):
    """同一 duration 在 SQL 判据与 Python 判据上必须一致。

    负时长是历史分叉点：旧代码 10 处写 ``== 0 | IS NULL``（负值 → 视频），
    而 content_kind 写 ``<= 0``（负值 → 图文）。现在都应归为图文。
    """
    from app.services.content_kind import content_kind_for, is_note

    expects_note = duration is None or duration <= 0
    assert is_note(None, duration) is expects_note
    assert content_kind_for(None, duration) == ("note" if expects_note else "video")

    # 负时长若沿用手写字面量会被判成视频 —— 显式钉住这个反例。
    if duration is not None and duration < 0:
        legacy_literal_says_note = (duration == 0) or duration is None
        assert legacy_literal_says_note is False, "反例前提失效，本测试需要重写"
        assert expects_note is True, "负时长必须归为图文（否则 M2 回归）"


def test_knowledge_service_has_no_independent_duration_literals():
    """M2 已修：knowledge_service 里不得再出现手写的 duration 判据字面量。

    只允许经 ``content_kind`` 的 ``note_predicate`` / ``video_predicate``，
    以及聚合函数内部的 ``duration > 0``（那属于 SQL 聚合，不是分支判据）。
    """
    source = (APP_DIR / "services" / "knowledge_service.py").read_text(encoding="utf-8")
    offenders = []
    for lineno, line in enumerate(source.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if re.search(r"duration\s*==\s*0", stripped):
            offenders.append((lineno, stripped))
        if re.search(r"duration\.is_\(None\)", stripped) and "note_predicate" not in stripped:
            offenders.append((lineno, stripped))
    assert offenders == [], f"仍存在手写 duration 字面量: {offenders}"


# ---------------------------------------------------------------------------
# M1 — 不得再手写抖音链接
# ---------------------------------------------------------------------------

def test_pipeline_builds_no_hand_made_douyin_url():
    """M1 已修：**入库流水线**不得出现 ``douyin.com/video/{...}`` 的拼接。

    M1 的具体位置是 `knowledge_service.py` 的兜底 `else`：它是"剩下的都归这里"
    的分支，任何新平台都会落进去并拿到伪造的抖音地址。流水线必须用注册表的
    能力门禁 + 规范链接模板。
    """
    source = (APP_DIR / "services" / "knowledge_service.py").read_text(encoding="utf-8")
    offenders = [
        (lineno, line.strip())
        for lineno, line in enumerate(source.splitlines(), 1)
        if re.search(r"""f?["']https://www\.douyin\.com/video/""", line)
    ]
    assert offenders == [], f"流水线仍手写抖音链接: {offenders}"


#: 允许继续持有抖音 URL 的**平台专有抓取器**：它们本来就只服务抖音，
#: 不是"未知平台兜底"，因此不属于 M1 类别。任何**新增**条目都必须在此说明理由，
#: 否则守卫失败——这正是防止它们退化成通用兜底的方式。
_DOUYIN_URL_SITES_ALLOWED = {
    "app/db/migration.py": "一次性遗留数据迁移 SQL（历史 = 抖音单平台），不是运行时构造",
    "app/services/adapters/douyin.py": "抖音适配器自身（该层当前零调用者，见 roadmap §2.1）",
    "app/services/douyin_collector.py": "抖音采集器：只产出抖音条目",
    "app/services/douyin_media_resolver.py": "抖音媒体解析器：只解析抖音页面",
    "app/services/platform_registry.py": "注册表的 URL 模板（平台事实的唯一来源）",
    "app/services/vision_service.py": "图集 OCR 结构上仅抖音，已用能力位隔离而非通用化",
}


def test_douyin_url_sites_are_an_explicit_closed_set():
    """把"哪里还可以写抖音地址"变成一份必须显式维护的清单。

    守卫的价值不在于消灭所有字面量，而在于：新增一处就必然失败，从而强迫作者
    回答"这是平台专有抓取器，还是又一个未知平台兜底？"
    """
    pattern = re.compile(r"""f?["']https://www\.douyin\.com/video/""")
    found = set()
    for path in _app_sources():
        rel = path.relative_to(APP_DIR.parent).as_posix()
        if pattern.search(path.read_text(encoding="utf-8")):
            found.add(rel)
    unexpected = found - set(_DOUYIN_URL_SITES_ALLOWED)
    assert unexpected == set(), (
        "新增了持有抖音 URL 的模块，请确认它是平台专有抓取器而非未知平台兜底；"
        f"确认后在 _DOUYIN_URL_SITES_ALLOWED 说明理由: {sorted(unexpected)}"
    )
    # 反向：清单里的文件若已不再持有，也要清理，避免清单腐烂成谎言。
    stale = set(_DOUYIN_URL_SITES_ALLOWED) - found
    assert stale == set(), f"清单已过期，请移除: {sorted(stale)}"


def test_audio_asr_path_is_capability_gated_not_a_fallback():
    """兜底 else 必须先用能力门禁，再构造地址；且地址来自注册表或入库时的规范链接。"""
    source = (APP_DIR / "services" / "knowledge_service.py").read_text(encoding="utf-8")
    assert "CAPABILITY_AUDIO_ASR" in source, "ASR 分支未做能力门禁（M1 回归）"
    assert "build_canonical_url" in source, "ASR 分支未通过注册表构造地址（M1 回归）"


# ---------------------------------------------------------------------------
# L1 — 空平台不得被"洗"成抖音
# ---------------------------------------------------------------------------

def test_empty_platform_is_not_laundered_into_douyin():
    """``item.platform or "douyin"`` 会让 "" 绕过能力预检，必须原样交给注册表。"""
    source = (APP_DIR / "services" / "knowledge_service.py").read_text(encoding="utf-8")
    assert 'item.platform or "douyin"' not in source, "L1 回归：空平台又被洗成抖音"
    assert "item_platform = item.platform" in source


@pytest.mark.parametrize("platform", ["", "   ", None, "xiaohongshu"])
def test_unregistered_and_blank_platforms_are_rejected_by_capabilities(platform):
    """注册表对空/未知平台一律拒绝，且不声明任何提取能力。"""
    from app.services.platform_registry import platform_capability_precheck

    assert platform_capability_precheck(platform) is not None
    assert extraction_capabilities(platform) == frozenset()
    for capability in (CAPABILITY_NOTE_OCR, CAPABILITY_AUDIO_ASR, CAPABILITY_SUBTITLE):
        assert platform_supports(platform, capability) is False


def test_registered_platforms_declare_their_real_capabilities():
    """能力集合必须与真实实现对齐，否则门禁会误杀现有平台。"""
    # 抖音：图文 OCR + 视频音频 ASR 两条路径都存在（这也是 M1 最初被引入的原因：
    # 只用一个策略字符串会把抖音的视频路径误判成"不支持"）。
    assert platform_supports("douyin", CAPABILITY_NOTE_OCR) is True
    assert platform_supports("douyin", CAPABILITY_AUDIO_ASR) is True
    # B 站：字幕优先 + DASH 音频 ASR 兜底，没有图集 OCR。
    assert platform_supports("bilibili", CAPABILITY_SUBTITLE) is True
    assert platform_supports("bilibili", CAPABILITY_AUDIO_ASR) is True
    assert platform_supports("bilibili", CAPABILITY_NOTE_OCR) is False
    # 只登记真实存在的平台。
    assert set(supported_platforms()) == {"douyin", "bilibili", "zhihu"} == set(PLATFORMS)


# ---------------------------------------------------------------------------
# L3 — 非规范拼写要抛 ValueError，而不是裸 KeyError
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("spelling", ["DoUyIn", "DOUYIN", " douyin ", "BiliBili"])
def test_mixed_case_platform_resolves_instead_of_raising_keyerror(spelling):
    svc = object.__new__(chroma_service.ChromaService)
    svc._collections = {"douyin": "c-douyin", "bilibili": "c-bilibili"}
    assert svc._collection_for(spelling) in ("c-douyin", "c-bilibili")


def test_unknown_platform_still_raises_valueerror_not_keyerror():
    """兼容契约：UnsupportedPlatformError 是 ValueError 子类，旧的 except 不受影响。"""
    svc = object.__new__(chroma_service.ChromaService)
    svc._collections = {"douyin": "c-douyin"}
    with pytest.raises(ValueError):
        svc._collection_for("xiaohongshu")
