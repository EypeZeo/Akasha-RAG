"""正文实质性阈值与 ``has_substantive_content`` 的唯一取值定义。

为什么需要这个模块
------------------
在收敛之前，"正文够不够实质"这件事在四处各写了一遍字面量：

===================================  ==================================
``knowledge_service`` (4 处)          ``>= 10``：检查点续传 / 图文 OCR 判定 /
                                     ASR 结果拒绝 / 最终入库拒绝
``knowledge_service`` (1 处)          ``>= 20``：图文仅标题兜底
``bilibili/content_fetcher.py``       ``SUBSTANTIVE_TEXT_MIN_CHARS = 50``
``db/migration.py``                   ``length(trim(...)) >= 50``
===================================  ==================================

同一个不变量（"拒绝仅标题入库"，防止标题污染向量库）有四个数字来源，
改一处就会静默放宽另一处。本模块只做一件事：**给这些数字一个名字**。

数值一字未改。阈值是入库的最后一道闸门，收敛字面量不等于可以顺手调参：
``tests/test_substantive_thresholds.py`` 把 10/20/50 三个值钉死。

``bilibili/content_fetcher.py`` 里的 ``SUBSTANTIVE_TEXT_MIN_CHARS`` 是 B 站
抓取器自己的运行时副本（那里是字幕/ASR 二选一的判定现场，测试也直接断言
它 == 50）。该文件不在本次改动的写入范围内，因此这里保留一个同值常量并由
测试断言两者相等——漂移会被测试而不是被用户发现。

关于 ``has_substantive_content``（死列的唯一 owner）
----------------------------------------------------
``ingestion_items.has_substantive_content`` 过去只在 4 处被写成 ``False``
（``favorites_service`` 的分 P 重建、``api/routes/knowledge.py`` 的两处重置、
``knowledge_service.delete_video``），**没有任何路径写 ``True``**，任何未来
读者读到的都是谎言。现在它的取值由本模块的 :func:`has_indexable_text` 定义，
``KnowledgeService.save_state`` 是唯一写入点：凡是通过该漏斗写正文，就同步写
这个标志，两个状态都写。于是：

- ``False`` ⇔ 该行没有可检索正文（未产生 / 已重置 / 被拒绝）；
- ``True``  ⇔ 该行已提交的正文通过了入库门禁（``>= 10`` 字符），因此会被
  切块并写入向量库；是否已经写完看 ``status == "done"``（Embedding 失败时
  检查点已提交、标志为 ``True``、状态为 ``failed``，这是可续传的正常状态）。

重置路径仍然直接写 ``False``，但它们同时把 ``transcript_text`` 清空，与
``has_indexable_text("") is False`` 逐位一致——不存在第二个取值定义。

历史口径说明：``db/migration.py`` 的遗留数据回填用的是 ``>= 50``（那是 B 站
的长正文口径，见文件内注释）。它是针对 v0.6 ``video_cache`` 的一次性回填，
不是本模块的活口径；两者不做合并，因为合并就意味着改动其中一个阈值数值。
"""
from __future__ import annotations

#: 可入库正文的最小字符数。低于此值的文本一律拒绝入库（"拒绝仅标题入库"）。
#: 历史出现位置：knowledge_service 的 ``>= 10``（检查点 / 图文 OCR / ASR 拒绝 /
#: 最终拒绝，共 4 处）。数值不得改动。
MIN_INDEXABLE_CHARS = 10

#: 图文（笔记）在没有 OCR 正文时，允许仅用标题兜底的最小标题长度。
#: 历史出现位置：knowledge_service 的 ``elif len(clean_title) >= 20``。
TITLE_FALLBACK_MIN_CHARS = 20

#: B 站字幕 / ASR 的实质正文门禁。
#: 运行时副本在 ``bilibili/content_fetcher.py::SUBSTANTIVE_TEXT_MIN_CHARS``，
#: 两者必须相等（由 tests/test_substantive_thresholds.py 断言）。
BILIBILI_SUBSTANTIVE_TEXT_MIN_CHARS = 50


def has_indexable_text(text: str | None) -> bool:
    """``text`` 去空白后是否达到可入库长度（:data:`MIN_INDEXABLE_CHARS`）。

    等价于历史上重复出现的 ``len(x.strip()) >= 10``，但同时接受 ``None``，
    调用方不需要各自写一次 ``not text or ...``。
    """
    return len((text or "").strip()) >= MIN_INDEXABLE_CHARS


def title_fallback_is_usable(title: str | None) -> bool:
    """图文仅标题兜底是否可用（:data:`TITLE_FALLBACK_MIN_CHARS`）。"""
    return len(title or "") >= TITLE_FALLBACK_MIN_CHARS
