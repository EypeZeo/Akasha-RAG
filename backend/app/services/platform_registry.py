"""平台事实注册表 —— 「这个平台是什么」的唯一事实来源。

在本模块出现之前，平台知识散落在十几处手写的 ``{"douyin", "bilibili"}`` 白名单、
``Literal[...]`` 联合类型，以及若干「else 就是抖音」的 URL 兜底里。这些兜底的真正
危险不是重复，而是**沉默**：给一个未登记的平台 id，代码会返回一个看起来完全正常
的抖音链接，而不是报错——用户看到的是一个指向错误作品的地址。

这里集中四类事实：

============================  =====================================================
``canonical_url_template``    作品规范链接；``None`` = 无法构造（调用方拿到空串，
                              而不是一个伪造的抖音链接）
``image_domains``             允许展示/抓取的图片域名（安全边界，见 core/external_urls.py）
``collection_name``           Chroma 分区名（``akasha_{platform}``）
``content_kind_by_duration``  形态判据是否由时长推导
``note_extraction``           是否实现了图文（图集 OCR）抽取
============================  =====================================================

只登记当前真实存在并已接入生产同步链路的平台。新增平台必须在这里补一行事实，不允许在业务代码里
再写 ``if platform == ...`` 的兜底分支。

分层说明
--------
``app/core/external_urls.py`` 只保留校验机制；图片域名事实通过
``register_image_domains()`` 在本模块 import 时注册进 core。方向始终是
services -> core，core 不反向 import services，因此没有循环依赖。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.core.external_urls import register_image_domains
from app.services.content_kind import is_note as _duration_based_is_note

#: Chroma 分区名前缀；``collection_name`` = 前缀 + 平台 id。
COLLECTION_PREFIX = "akasha"

#: 正文提取能力。流水线的分支**不能**靠"剩下都归抖音"来决定，必须查这张表。
#: 注意这是**能力集合**：抖音同时具备图集 OCR（图文）与音频 ASR（视频）两条路径，
#: 只用一个策略字符串会把它的视频路径误判成不支持。
CAPABILITY_NOTE_OCR = "note_ocr"          # 图集 OCR（vision_service，目前仅抖音）
CAPABILITY_AUDIO_ASR = "audio_asr"        # 下载音频 → ASR（抖音视频、B站兜底）
CAPABILITY_SUBTITLE = "subtitle"          # 平台字幕（B站）
CAPABILITY_ARTICLE_TEXT = "article_text"  # 已抓取的文章/回答正文


class UnsupportedPlatformError(ValueError):
    """请求了未登记的平台。

    继承 ``ValueError`` 是刻意的：既有的 ``except ValueError`` 调用方（路由层的
    400 转换、适配器工厂等）行为不变，但类型上现在可以单独捕获。
    """


@dataclass(frozen=True)
class PlatformFacts:
    """一个平台的全部声明式事实。"""

    platform: str
    display_name: str
    canonical_url_template: str | None
    image_domains: frozenset[str]
    collection_name: str
    content_kind_by_duration: bool
    capabilities: frozenset[str] = frozenset()

    @property
    def note_extraction(self) -> bool:
        """是否实现了图文（图集 OCR）抽取 —— **由能力集合派生**，不单独存字段。

        曾经它是一个独立布尔量，与 ``capabilities`` 并存，于是同一个事实有两个
        归属：一个平台可以声明 ``CAPABILITY_NOTE_OCR`` 却忘了把布尔量置真，结果
        被静默改道去下载音频做 ASR。现在只保留能力集合这一处声明。
        """
        return CAPABILITY_NOTE_OCR in self.capabilities


def _facts(platform: str, display_name: str, template: str | None, domains: frozenset[str],
           *, content_kind_by_duration: bool = True,
           capabilities: frozenset[str] = frozenset()) -> PlatformFacts:
    """构造一条平台事实，分区名由前缀统一推导，杜绝两处各写一遍 ``akasha_``。"""
    return PlatformFacts(
        platform=platform,
        display_name=display_name,
        canonical_url_template=template,
        image_domains=domains,
        collection_name=f"{COLLECTION_PREFIX}_{platform}",
        content_kind_by_duration=content_kind_by_duration,
        capabilities=capabilities,
    )


#: 唯一事实来源。只允许出现真实存在、并有适配器/抓取实现的平台。
PLATFORMS: dict[str, PlatformFacts] = {
    "douyin": _facts(
        "douyin",
        "抖音",
        "https://www.douyin.com/video/{remote_item_id}",
        frozenset(
            {
                "douyinpic.com",
                "byteimg.com",
                "pstatp.com",
                "ibytedtos.com",
                "bytedance.com",
                "bytedcdn.com",
                "zjcdn.com",
            }
        ),
        capabilities=frozenset({CAPABILITY_NOTE_OCR, CAPABILITY_AUDIO_ASR}),
    ),
    "bilibili": _facts(
        "bilibili",
        "哔哩哔哩",
        "https://www.bilibili.com/video/{remote_item_id}",
        frozenset({"hdslb.com", "biliimg.com"}),
        capabilities=frozenset({CAPABILITY_SUBTITLE, CAPABILITY_AUDIO_ASR}),
    ),
    "zhihu": _facts(
        "zhihu",
        "知乎",
        None,
        frozenset({"zhihu.com", "zhimg.com", "zhimg.com.cn"}),
        capabilities=frozenset({CAPABILITY_ARTICLE_TEXT}),
    ),
}


def supported_platforms() -> tuple[str, ...]:
    """当前真实支持的平台 id（顺序稳定：用于分区分页、评测清单等有序输出）。"""
    return tuple(PLATFORMS)


def try_get_platform(platform: str | None) -> PlatformFacts | None:
    """查询用：未登记返回 ``None``，不抛异常。

    过滤/展示路径不应该因为一个历史脏值而 500，所以这些路径用本函数；
    构造链接、写向量库这类「必须知道平台」的路径用 :func:`get_platform`。
    """
    if not isinstance(platform, str):
        return None
    return PLATFORMS.get(platform.strip().lower())


def _unsupported_message(platform: str | None) -> str:
    return f"不支持的平台: '{platform}'，当前支持: {', '.join(supported_platforms())}"


def get_platform(platform: str | None) -> PlatformFacts:
    """必须成功的平台查询；未登记时抛 :class:`UnsupportedPlatformError`。"""
    facts = try_get_platform(platform)
    if facts is None:
        raise UnsupportedPlatformError(_unsupported_message(platform))
    return facts


def build_canonical_url(platform: str | None, remote_item_id: str | None, fallback_url: str = "") -> str:
    """构造作品规范链接，**绝不**伪造另一个平台的链接。

    - 已登记且模板/id 齐备：按模板生成（douyin / bilibili 与历史实现逐字一致）
    - 未登记、无模板、或 id 为空：返回 ``fallback_url``（默认空串）

    历史实现是 ``"bilibili 模板" if platform == "bilibili" else "douyin 模板"``，
    即任何未知平台都会拿到一个抖音链接。现在未知平台只会拿到空串——调用方要么
    有真实 URL 可传 ``fallback_url``，要么诚实地展示「无链接」。
    """
    item_id = "" if remote_item_id is None else str(remote_item_id).strip()
    facts = try_get_platform(platform)
    if facts is not None and facts.canonical_url_template and item_id:
        return facts.canonical_url_template.format(remote_item_id=item_id)
    return fallback_url or ""


def platform_capability_precheck(platform: str | None) -> str | None:
    """入库前的平台能力预检：``None`` = 可以继续，否则返回可直接展示的拒绝原因。

    存在的意义是「别在花钱之后才发现平台不支持」：``chroma_service`` 会在音频下载、
    付费 ASR、Embedding 全部跑完之后才因平台不受支持而抛错。调用方应在这些阶段
    **之前**调用本函数快速失败。
    """
    if try_get_platform(platform) is None:
        return _unsupported_message(platform)
    return None


def is_note_kind(platform: str | None, duration: int | None) -> bool:
    """形态判据的唯一入口：先问平台事实，再把比较规则交给 ``content_kind.py``。"""
    facts = try_get_platform(platform)
    if facts is not None and not facts.content_kind_by_duration:
        return False
    return _duration_based_is_note(platform, duration)


def needs_note_extraction(platform: str | None, duration: int | None) -> bool:
    """图文抽取（图集 OCR）路径是否适用：形态是图文 **且** 该平台实现了抽取。

    图集抽取目前只有抖音实现（``vision_service`` 的抓取参数写死 douyin），所以
    B 站上一个零时长条目虽然形态判据是「图文」（见 ``content_kind`` 的平台无关
    规则），也不能被当成抖音图集去解析——入库分支必须两者同时成立。
    """
    facts = try_get_platform(platform)
    if facts is None or not facts.note_extraction:
        return False
    return is_note_kind(platform, duration)


def extraction_capabilities(platform: str | None) -> frozenset[str]:
    """该平台声明的正文提取能力集合；未登记平台返回空集（= 什么都不能做）。

    流水线用它决定走哪条提取路径，而不是用「剩下的都归抖音」这种隐式兜底。
    """
    facts = try_get_platform(platform)
    return facts.capabilities if facts is not None else frozenset()


def platform_supports(platform: str | None, capability: str) -> bool:
    """该平台是否声明了某项正文提取能力。"""
    return capability in extraction_capabilities(platform)


#: FastAPI 查询参数用的平台过滤字面量。由注册表**推导**而不是各处手写，任何新增
#: 平台的改动都只需要动本文件；``tests/test_platform_registry.py`` 另外断言它与
#: supported_platforms() 一致，防止有人改回硬编码。
PlatformFilter = Literal["all", *supported_platforms()]

_PLATFORM_FILTER_VALUES: tuple[str, ...] = ("all", *supported_platforms())


def _register_core_image_domains() -> None:
    """把图片域名事实推给 core 的校验机制（方向：services -> core）。"""
    for facts in PLATFORMS.values():
        register_image_domains(facts.platform, facts.image_domains)


_register_core_image_domains()
