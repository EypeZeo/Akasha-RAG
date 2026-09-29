"""平台事实注册表 + 内容形态判据的契约测试。

这些测试锁定的不是实现细节，而是三件容易悄悄退化的事：
1. 未登记平台**不能**再拿到一个伪造的抖音链接（历史 ``else`` 兜底的行为）。
2. FastAPI/评测层手写的平台字面量必须与注册表一致（漂移即失败）。
3. 图文/视频判据只有一处（``content_kind``），0 与负时长必须同归类。
"""
from __future__ import annotations

import re
from typing import get_args

import pytest

from app.api.routes import chat as chat_routes
from app.api.routes.favorites import sync_favorites
from app.core.external_urls import registered_image_platforms, safe_platform_image_url
from app.services import chroma_service, content_kind, rag_evaluation
from app.services.adapters import factory
from app.services.platform_registry import (
    PLATFORMS,
    PlatformFilter,
    UnsupportedPlatformError,
    build_canonical_url,
    get_platform,
    is_note_kind,
    needs_note_extraction,
    platform_capability_precheck,
    supported_platforms,
    try_get_platform,
)

DOUYIN = "douyin"
BILIBILI = "bilibili"
ZHIHU = "zhihu"


# --------------------------------------------------------------------------
# 1. 注册表：查询 / 白名单
# --------------------------------------------------------------------------

def test_registry_lists_all_connected_platforms():
    assert supported_platforms() == (DOUYIN, BILIBILI, ZHIHU)
    assert set(PLATFORMS) == {DOUYIN, BILIBILI, ZHIHU}


@pytest.mark.parametrize("platform", [DOUYIN, BILIBILI, ZHIHU, "  DouYin  ", "BILIBILI"])
def test_get_platform_normalises_case_and_whitespace(platform):
    assert get_platform(platform).platform in (DOUYIN, BILIBILI, ZHIHU)


def test_get_platform_raises_for_unknown_platform():
    for unknown in ("xiaohongshu", "", "   ", None):
        with pytest.raises(UnsupportedPlatformError):
            get_platform(unknown)
    # 必须仍是 ValueError：路由层/旧调用方的 except ValueError 不能失效。
    assert issubclass(UnsupportedPlatformError, ValueError)


def test_try_get_platform_is_silent_for_unknown_platform():
    for unknown in ("xiaohongshu", "", None, 123):
        assert try_get_platform(unknown) is None
    assert try_get_platform("douyin") is not None


def test_every_platform_fact_is_complete():
    for facts in PLATFORMS.values():
        assert facts.display_name
        assert facts.image_domains, "图片域名白名单不能为空，否则该平台头像/图集全部被拒"
        assert all(domain == domain.lower() and domain for domain in facts.image_domains)
        assert facts.content_kind_by_duration is True, "当前两个平台的形态都由时长推导"


# --------------------------------------------------------------------------
# 2. canonical URL：与历史行为逐字一致，且未登记平台不伪造抖音链接
# --------------------------------------------------------------------------

def test_canonical_url_is_byte_identical_to_the_historical_templates():
    assert build_canonical_url(DOUYIN, "7123456789") == "https://www.douyin.com/video/7123456789"
    assert build_canonical_url(BILIBILI, "BV1xx411c7mD") == "https://www.bilibili.com/video/BV1xx411c7mD"


def test_canonical_url_for_unknown_platform_is_empty_not_douyin():
    for unknown in ("xiaohongshu", "youtube", "douban", "", None):
        url = build_canonical_url(unknown, "some-remote-id")
        assert url == ""
        assert "douyin.com" not in url


def test_canonical_url_uses_fallback_only_when_it_cannot_build_one():
    assert build_canonical_url("zhihu", "1", "https://www.zhihu.com/question/1") == "https://www.zhihu.com/question/1"
    # 能构造时，fallback 不参与。
    assert build_canonical_url(DOUYIN, "1", "https://example.test/x") == "https://www.douyin.com/video/1"
    # 缺少 id 时不产出 "…/video/" 这类半截链接。
    assert build_canonical_url(DOUYIN, "") == ""
    assert build_canonical_url(DOUYIN, None, "https://fallback.test/1") == "https://fallback.test/1"


# --------------------------------------------------------------------------
# 3. Chroma 分区名 / 图片域名白名单：注册表是唯一来源
# --------------------------------------------------------------------------

def test_collection_names_stay_akasha_prefixed():
    assert get_platform(DOUYIN).collection_name == "akasha_douyin"
    assert get_platform(BILIBILI).collection_name == "akasha_bilibili"
    assert chroma_service.ChromaService._collection_name(DOUYIN) == "akasha_douyin"
    assert chroma_service.ChromaService._collection_name(BILIBILI) == "akasha_bilibili"


def test_chroma_rejects_an_unregistered_platform_before_touching_storage():
    svc = object.__new__(chroma_service.ChromaService)
    svc._collections = {DOUYIN: object(), BILIBILI: object()}
    with pytest.raises(ValueError):
        svc._collection_for("xiaohongshu")


def test_upsert_requires_platform_so_it_cannot_default_to_douyin():
    """``platform`` 是无默认值的必填参数：漏传在**调用点**就炸，不可能静默写抖音分区。

    历史实现是 ``platform: str = "douyin"``，任何漏传的调用点都会把正文写进抖音
    分区，既不报错也无法事后区分。这里把"必须显式声明平台"钉成**签名层面**的
    结构性质（TypeError），而不是只在运行时对真实服务做一次检查——前者对所有
    调用者都成立，包括未来的新调用点。
    """
    import inspect

    params = inspect.signature(chroma_service.ChromaService.upsert_video_chunks).parameters
    assert params["platform"].default is inspect.Parameter.empty, "platform 不得有默认值"

    svc = object.__new__(chroma_service.ChromaService)
    svc._collections = {DOUYIN: object(), BILIBILI: object()}
    with pytest.raises(TypeError):
        svc.upsert_video_chunks("123", "t", ["body"], [[1.0, 0.0]])


def test_core_image_domains_come_from_the_registry_without_drift():
    assert registered_image_platforms() == tuple(sorted(supported_platforms()))
    # 边界仍然生效（这是把白名单搬进注册表后最需要盯住的性质）。
    assert safe_platform_image_url(BILIBILI, "https://i0.hdslb.com/face.jpg?x=1") == "https://i0.hdslb.com/face.jpg?x=1"
    assert safe_platform_image_url(DOUYIN, "https://p3-sign.douyinpic.com/a.webp") == "https://p3-sign.douyinpic.com/a.webp"
    assert safe_platform_image_url(BILIBILI, "https://evilhdslb.com/face.jpg") == ""
    assert safe_platform_image_url(BILIBILI, "https://127.0.0.1/face.jpg") == ""
    assert safe_platform_image_url("xiaohongshu", "https://i0.hdslb.com/face.jpg") == ""


# --------------------------------------------------------------------------
# 4. 平台字面量与注册表一致（防止有人改回硬编码）
# --------------------------------------------------------------------------

def test_fastapi_platform_filter_agrees_with_the_registry():
    assert get_args(PlatformFilter) == ("all", *supported_platforms())


def test_ask_request_platform_union_is_the_registry_filter():
    # 注解是 `PlatformFilter | None`，取出其中的 Literal 再比对。
    annotation = chat_routes.AskRequest.model_fields["platform"].annotation
    literal = next(arg for arg in get_args(annotation) if get_args(arg))
    assert get_args(literal) == ("all", *supported_platforms())


def test_evaluation_and_adapter_layers_are_derived_from_the_registry():
    assert factory.list_supported_platforms() == list(supported_platforms())
    assert rag_evaluation.SUPPORTED_PLATFORMS == frozenset(("all", *supported_platforms()))
    assert [get_platform(p).collection_name for p in supported_platforms()] == [
        "akasha_douyin", "akasha_bilibili", "akasha_zhihu",
    ]


#: 消灭过的「沉默兜底」写法。任何一条重新出现都意味着平台知识又散回了业务代码。
_FORBIDDEN_PLATFORM_PATTERNS = (
    (r'Literal\["all",\s*"douyin"', "平台联合类型必须用 PlatformFilter（由注册表推导）"),
    (r'[\{\(\[]"douyin",\s*"bilibili"', "平台白名单集合必须用 supported_platforms()"),
    (r'else f"https://www\.douyin\.com/video/', "else 分支不得伪造抖音链接"),
    (r'platform:\s*str\s*=\s*"douyin"', "平台参数不得静默默认成抖音"),
    (r'if platform ==\s*"(douyin|bilibili)"\s*else', "平台三元表达式必须用 build_canonical_url"),
)


def test_no_platform_knowledge_leaked_back_into_business_code():
    """源码级漂移护栏：这五类写法在本仓库已经全部消灭，不允许复活。

    豁免：``platform_registry.py`` 是事实来源本身；
    ``douyin_collector.py`` 的 ``platform: str = "douyin"`` 是抖音采集器自己
    快照数据类的字段默认值（它不是通用的平台解析入口）。
    """
    from pathlib import Path

    app_root = Path(__file__).resolve().parents[1] / "app"
    exempt = {"platform_registry.py", "douyin_collector.py"}
    offenders: list[str] = []
    for path in sorted(app_root.rglob("*.py")):
        if path.name in exempt:
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue
            for pattern, reason in _FORBIDDEN_PLATFORM_PATTERNS:
                if re.search(pattern, line):
                    offenders.append(f"{path.relative_to(app_root)}:{lineno}: {reason}: {line.strip()}")
    assert offenders == [], "平台知识重新散落回业务代码:\n" + "\n".join(offenders)


def test_the_drift_guard_patterns_really_catch_the_historical_writing_styles():
    """护栏本身也要被测：抓不住历史写法的正则只是一行装饰。

    样例逐字取自本次改动前的真实代码行（favorites/knowledge/rag/chroma 路由）。
    """
    samples = [
        'platform: Literal["all", "douyin", "bilibili"] = "all"',
        'if platform not in {"douyin", "bilibili", "all"}:',
        'else f"https://www.douyin.com/video/{vid}"',
        'platform: str = "douyin", content_item_id: int | None = None, canonical_url: str = "",',
        'resolved_url = canonical_url or (f"https://www.bilibili.com/video/{platform_item_id}" '
        'if platform == "bilibili" else f"https://www.douyin.com/video/{platform_item_id}")',
    ]
    assert len(samples) == len(_FORBIDDEN_PLATFORM_PATTERNS)
    for text, (pattern, _reason) in zip(samples, _FORBIDDEN_PLATFORM_PATTERNS):
        assert re.search(pattern, text), f"护栏漏掉了历史写法: {text}"


@pytest.mark.asyncio
async def test_sync_route_rejects_unknown_platform_using_the_registry():
    assert await sync_favorites(platform="unknown", db=None) == {"success": False, "message": "不支持的平台"}


# --------------------------------------------------------------------------
# 5. 形态判据：只有一处、0 与负时长同归类
# --------------------------------------------------------------------------

@pytest.mark.parametrize("platform", [DOUYIN, BILIBILI, "zhihu", None, ""])
@pytest.mark.parametrize("duration", [0, -1, -3600])
def test_zero_and_negative_durations_are_notes_for_every_platform(platform, duration):
    assert content_kind.is_note(platform, duration) is True
    assert content_kind.content_kind_for(platform, duration) == "note"


@pytest.mark.parametrize("platform", [DOUYIN, BILIBILI, "zhihu", None, ""])
@pytest.mark.parametrize("duration", [1, 42, 3600])
def test_positive_durations_are_videos_for_every_platform(platform, duration):
    assert content_kind.is_note(platform, duration) is False
    assert content_kind.content_kind_for(platform, duration) == "video"


def test_missing_duration_counts_as_a_note():
    assert content_kind.content_kind_for(DOUYIN, None) == "note"
    assert content_kind.is_note(DOUYIN, None) is True


def test_registry_note_kind_defers_to_the_shared_evaluator():
    """注册表不是第二套判据：它只查平台事实，比较规则仍来自 content_kind。"""
    for duration in (None, -5, 0, 1, 600):
        for platform in (DOUYIN, BILIBILI):
            assert is_note_kind(platform, duration) is content_kind.is_note(platform, duration)
            assert needs_note_extraction(platform, duration) is (
                content_kind.is_note(platform, duration) and get_platform(platform).note_extraction
            )


def test_note_extraction_is_douyin_only_so_the_pipeline_cannot_misroute():
    """B 站零时长条目形态上是图文，但没有图文抽取实现——两者必须分开表达。"""
    assert is_note_kind(BILIBILI, 0) is True
    assert needs_note_extraction(BILIBILI, 0) is False
    assert needs_note_extraction(DOUYIN, 0) is True
    assert needs_note_extraction(DOUYIN, 120) is False
    assert needs_note_extraction("zhihu", 0) is False


def test_pipeline_gate_matches_the_historical_expression_on_every_real_input():
    """回归锁定：旧表达式 ``item_platform == "douyin" and item.duration in (0, None)``。

    None / 0 / 正时长必须逐位一致（B 站零时长条目仍走 B 站字幕路径，不会被当成
    抖音图集去解析）。唯一刻意的偏差是负时长——旧写法在列表口径算图文、在流水线
    算视频，本次收敛为图文。
    """
    for duration in (None, 0, 1, 60, 3600):
        for platform in (DOUYIN, BILIBILI, "zhihu"):
            historical = platform == DOUYIN and duration in (0, None)
            assert needs_note_extraction(platform, duration) is historical, (platform, duration)
    assert needs_note_extraction(DOUYIN, -5) is True  # 收敛后的偏差，仅此一处


def test_image_domain_sets_are_unchanged_from_the_pre_registry_implementation():
    """域名集合逐字取自改动前的 ``app/core/external_urls.py``（git show HEAD:…）。"""
    assert PLATFORMS[BILIBILI].image_domains == frozenset({"hdslb.com", "biliimg.com"})
    assert PLATFORMS[DOUYIN].image_domains == frozenset({
        "douyinpic.com", "byteimg.com", "pstatp.com", "ibytedtos.com",
        "bytedance.com", "bytedcdn.com", "zjcdn.com",
    })


def test_sql_predicates_are_the_exact_complement_of_the_python_rule():
    """video + note == total：SQL 口径必须与 content_kind 完全互补（含 NULL）。"""
    from sqlalchemy import Column, Integer, MetaData, Table, create_engine, func, select

    engine = create_engine("sqlite://")
    # 独立最小表：duration 可空，用来覆盖遗留库里的 NULL 行。
    legacy = Table("legacy_durations", MetaData(), Column("duration", Integer))
    legacy.create(engine)
    with engine.begin() as conn:
        conn.execute(legacy.insert(), [{"duration": value} for value in (None, -1, 0, 1, 600)])
    with engine.connect() as conn:
        note = conn.scalar(select(func.count()).select_from(legacy).where(content_kind.note_predicate(legacy.c.duration)))
        video = conn.scalar(select(func.count()).select_from(legacy).where(content_kind.video_predicate(legacy.c.duration)))
        total = conn.scalar(select(func.count()).select_from(legacy))

    assert int(note) == 3  # NULL / -1 / 0
    assert int(video) == 2  # 1 / 600
    assert int(video) + int(note) == int(total)


def test_platform_capability_precheck_is_the_early_gate():
    assert platform_capability_precheck(DOUYIN) is None
    assert platform_capability_precheck(BILIBILI) is None
    assert platform_capability_precheck(ZHIHU) is None
    reason = platform_capability_precheck("xiaohongshu")
    assert reason and "xiaohongshu" in reason and DOUYIN in reason
