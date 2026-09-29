# Akasha-RAG 平台接入路线图 v4.0（修正版）

> **基线提交**：`f507af5` (main) · 本轮在该基线上新增 3 个源码模块、4 个测试文件，并修改 13 个既有文件（全部为后端 + 文档；前端**未改动**，因为前端改动只在真正接入新平台时才需要，见 §3 G4）
> **测试基线**：`cd backend; .venv\Scripts\python.exe -m pytest -q` → **436 passed, 7 subtests passed**（实测）
> **本轮收尾状态**：**550 passed, 7 subtests passed**（436 → 550，+114 全部为新增测试，零失败）；CI lint 门禁 `ruff check --select E9,F63,F7,F82 app tests` → `All checks passed!`
> **本文档取代**：`PLATFORM_ROADMAP_v3.md`（其失效原因见附录 A：v3 逐条勘误；v3 已就地改为指向本文档的墓碑页）
> **证据归档**：`docs/audits/WS-VERIFICATION.md`（WS3 首轮对抗性复验，481 行）、`docs/audits/WS4-REVERIFY.md`（收尾改动复验，468 行）
> **修复状态**：第 1 章为**已在本轮实施并验证**的真实缺陷修复；第 2 章为**需要先做决策、不得直接开工**的未决项

---

## 0. 本文档的编写规则

v3 的根本问题不是结论错，而是**每一个结论都不可核验**。因此本版强制以下规则：

| 规则 | 含义 |
|---|---|
| R1 | 每条关于现有代码的陈述必须给出 `文件:行号`，且该处代码必须真的存在 |
| R2 | 不得声称"已存在"某个注册机制/契约，除非能在代码里指出调用点 |
| R3 | 每条验收标准必须**可被证伪**（出现反例即判失败） |
| R4 | 未实测的外部 API 一律标注 `未实测`，不得写"✅ 可用" |
| R5 | 工期估算必须标注依据；无依据则写"未估算" |

> v3 违反 R1 至少 6 处、R2 共 2 处、R3 共 1 处（见附录 A）。本版不重复这些错误。

---

## 1. 已实施：真实缺陷修复

以下缺陷**全部经代码阅读确认**，且**不依赖任何新平台**即可复现——换言之，这两个平台现在就有问题。

### 1.1 缺陷总表

| # | 缺陷 | 证据（**改动前**的行号） | 后果 | 状态 |
|---|---|---|---|---|
| D1 | 适配器层是**死代码** | `get_adapter`/`list_supported_platforms` 全仓库**零调用者**（仅 `adapters/__init__.py` 再导出）；`fetch_item_content` 仅存在于 `base.py:122` 与两个实现，无调用点 | v3 计划把 4 个新平台建在一套从未接线的系统上 | 见 §2.1（治理项，需决策） |
| D2 | `is_note` **两处规则互相矛盾** | `knowledge_service.py:293` = `platform=="douyin" and duration in (0,None)`；`knowledge_service.py:859` = `duration == 0 or duration is None`（无平台条件） | 同一行数据在列表页与入库流水线的判定不同（负数时长最明显）。**实测修正**：原表述"真实 B 站视频会掉进抖音图集分支"**未能构造出可复现实例**——B 站 `duration` 为 0 时 `part_count > 1`，实际走多P路径。该表述已撤回，改为**潜在**路由漏洞：任何"零时长 + 平台为 B 站"的条目一旦分类为 note，就会进入 OCR 分派，而 `vision_service` 6 处硬编码 `"douyin"` | **已修** —— 注：`needs_note_extraction()` 是首轮做法；WS4 指出它与 `capabilities` 集合构成同一事实的两个归属（M1-b），现已改为**由能力集合派生** |
| D3 | `content_kind` **三条定义分叉**（不是两条） | ① `favorites_service.py:477` = `duration_val <= 0`；② `knowledge_service.py:859` = `duration == 0 or None`；③ `migration.py:349` 的遗留迁移 SQL = `CASE WHEN COALESCE(duration, 0) <= 0 THEN 'note' ELSE 'video' END` | `duration` 为 `NOT NULL DEFAULT 0`（`entities.py:110`），负数时长在同步路径与迁移 SQL 记为 `note`、在列表路径记为 `video`，**同一行数据三种解读** | **已修** —— 但见 §1.4 **M2**：首轮只收敛了 4 处，WS3 复验又找出 **10 处**独立字面量，已二次收敛 |
| D4 | canonical_url **静默兜底抖音**（4 处） | `favorites_service.py:478-482`、`chroma_service.py:86`、`chroma_service.py:146`、`rag_service.py:1166-1168`、`rag_service.py:1183` | 未知平台 ID 被拼成 `https://www.douyin.com/video/<id>`，**写进向量元数据、并作为来源链接展示给用户** | **已修** |
| D5 | Chroma 白名单**校验太晚** | `chroma_service.py:56` 抛 `ValueError`，而唯一流水线调用点在 `knowledge_service.py:470` — 位于**音频下载、付费 ASR、Embedding 全部完成之后** | 不支持的平台每条内容烧掉一整轮付费配额才失败 | **已修**（前移到能力预检） |
| D6 | `upsert_video_chunks` 的 `platform` **有默认值 `"douyin"`** | `chroma_service.py:70`。**实测修正**：`tests/test_chroma_writes.py:33`、`:56` 用 4 个位置参数调用、**不传 platform**（`object.__new__` + 单 collection 替身），因此该参数**无法直接改为必填** | 任何漏传的调用点静默写入抖音集合（跨平台污染，且不报错） | **已修** —— `platform` 现为**无默认值的必填参数**，漏传在调用点直接 `TypeError`；那两处旧测试替身已补 `platform="douyin"`；测试改为断言**签名层面无默认值**（`inspect.signature`），对所有未来调用点成立 |
| D7 | `transcript_source` **系统性撒谎** | `favorites_service.py:548`、`:612` 在**任何正文存在之前**就把 `ContentPart` 写成 `transcript_source="whisper_asr"`；`entities.py:352` 默认值同；`migration.py:322` 列默认值同。全仓库**无任何代码写入其他值** | B 站字幕来源的正文被**永久**标记为 Whisper ASR，无法区分 | **已修** |
| D8 | `has_substantive_content` 是**死列** | 仅 4 处写入，全部为 `False`（`favorites_service.py:598`、`api/routes/knowledge.py:391`、`:427`、`knowledge_service.py:926`）。**无任何路径写入 `True`** | 任何未来读者都会读到谎言；真实门禁是三个散落的字面量 10/20/50 | **部分修复** —— 取值现在是真的（`save_state` 是唯一计算者，两态都写），但见 §1.4 **L4**：仍有 4 处重置路径直写 `False`，且**全仓库零读者**。它不是"修好了"，是"不再撒谎但没人用" |
| D9 | 平台白名单**硬编码 13 处** | `api/routes/chat.py:56`、`api/routes/favorites.py:36`、`:165`、`:188`、`api/routes/knowledge.py:38`、`:98`、`:248`、`:325`、`chroma_service.py:16`、`rag_evaluation.py:25`、`:541`、`:612`、`adapters/factory.py:41` | 加平台时漏改任一处 → 请求**静默 422 或返回空结果** | **已修**（收敛到注册表） |

### 1.2 修复的最小充分边界

**不新增平台，不新增抽象层，只做"让加平台这件事只有一个 owner"。** 右侧标注实施后的**真实**状态，而非设计意图。

| # | 动作 | 实施后真实状态 |
|---|---|---|
| 1 | **`platform_registry.py`（新增）** — 平台事实的单一来源：canonical URL 模板、图片域名白名单、Chroma collection 名、显示名、能力预检、**正文提取能力集合**。`get_platform()` 对未知平台抛 `UnsupportedPlatformError`（不再返回抖音兜底）；`build_canonical_url()` 无模板时返回 `""`（绝不伪造 URL）；两个现有平台行为逐字节不变 | ✅ 完成。WS3 用 492 个用例对照 HEAD 逐一比对图片域名边界，**0 处不一致** |
| 2 | **`content_kind.py`（新增）** — note/video 判定的唯一求值器，`duration <= 0 → note`（含 NULL），与 `favorites_service.py:925` 的文档化意图一致；并提供与 Python 判据等价的 SQL predicate | ⚠️ **首轮不完整**。WS1 只把 4 处接上去，WS3 又找出 **10 处**独立字面量（M2），Lead 二次收敛后才真正单一。这是本轮最典型的"报告说完成、实际没完成" |
| 3 | **`platform_capability_precheck()` 前移到付费阶段之前** — 不支持的平台在下载/ASR/Embedding 之前被拒绝，并以 `error_code="unsupported_platform"` 落库 | ✅ 完成（对已登记/未登记平台）。但 WS3 发现空串平台可绕过（L1），Lead 已修 |
| 4 | **`upsert_video_chunks` 的 `platform` 改为必填** | ✅ 完成，但**任务书原有的前提是错的**：原以为"两个调用点都传了"，实际 `tests/test_chroma_writes.py:33,:56` 不传。最终做法是 `platform` **无默认值**（漏传即 `TypeError`），并补上那两处测试调用 |
| 5 | **`transcript_source` 不再在创建时预写** — 创建 `ContentPart` 是"计划"不是"正文" | ✅ 完成（用 `""` 哨兵而非 NULL，理由见 §1.4 与 WS3 报告：升级库上仍烘焙着 `DEFAULT 'whisper_asr'`，ORM 不显式写入就会让谎言复活）。⚠️ 真实来源只能写 `"unknown"`，因为 `fetch_transcript()` 只返回 `str`，字幕与 ASR 两条路径无法区分——**未假装能区分** |
| 6 | **`has_substantive_content` 得到唯一值计算者** | ⚠️ **部分**。取值现在是真的（`save_state` 同时写两态），但仍有 4 处重置路径直写 `False`，且**全仓库零读者**（L4）。不要读成"修好了" |
| 7 | **10/20/50 收敛为具名常量** — **数值一字不改** | ✅ 完成且值未变，WS3 断言 10/20/50 精确不变、无任何门禁放宽。残留 `adapters/douyin.py` 的副本（惰性代码，L2） |

### 1.3 验收标准（可证伪）

| 编号 | 断言 | 证伪条件 | 现在的守卫 |
|---|---|---|---|
| A1 | `pytest -q` ≥ 436 passed | 任何原有测试失败或总数下降 | 当前 **550 passed**；WS3/WS4 各自复跑确认 |
| A2 | 未知平台无法解析出抖音 URL | 全仓库仍有 `platform` 三元表达式可产出 `douyin.com/video/` | `test_douyin_url_sites_are_an_explicit_closed_set`：把"哪里还可以写抖音地址"变成必须显式维护的闭集，新增一处即失败；且反向检查清单不腐烂 |
| A3 | `upsert_video_chunks` 缺 `platform` 调用即报错 | 存在不传 `platform` 的调用点 | `test_upsert_requires_platform_so_it_cannot_default_to_douyin`：断言**签名层面无默认值**（对全部未来调用点成立，非单点运行时检查） |
| A4 | note/video 判定只有一条规则 | 全仓库仍有独立的 duration 判定分支 | `test_knowledge_service_has_no_independent_duration_literals`（源码守卫）+ `test_note_and_video_predicates_partition_the_row_set`（真建表计数，断言 `note + video == total`） |
| A5 | 图片安全边界未削弱 | 后缀欺骗 / IP 字面量 / 带凭证 / 非 443 / 非白名单平台任一不再被拒绝 | WS3 对照 HEAD 跑了 **492 个用例，0 处不一致** |
| A6 | 阈值仍为 10/20/50 且无一放宽 | 任何此前被拒绝的正文现在能入库 | WS3 断言数值精确不变、无门禁放宽；`substantive.py` 的漂移守卫 |
| A7 | 无 `ContentPart` 在正文产生前声称正文来源 | 创建路径仍写入 `"whisper_asr"` | WS3 在**真实升级库**上验证：既有库仍烘焙 `DEFAULT 'whisper_asr'`，而 ORM 显式写入 `''`，故哨兵有效 |

> **A6 是最重要的一条**。它守护的是"拒绝仅标题入库"这一核心不变量——正是它防止标题污染向量库。**加平台绝不允许动它。**

### 1.4 独立验证发现的缺陷：处置台账

三个工作流各自声称完成后，第四方（WS3）做了对抗性复验，**推翻了其中三条声明**。完整证据见 `docs/audits/WS-VERIFICATION.md`（481 行，每条附命令与原始输出）。处置如下：

| 发现 | 严重度 | 说明 | 处置 |
|---|---|---|---|
| **M1** 兜底 `else` 手写抖音链接 | MEDIUM | `knowledge_service.py` 的 `else` 分支直写 `f"https://www.douyin.com/video/{id}"`。当时只有抖音会走到，所以不报错；但注册第三个平台后它就成了落点。WS1 的守卫测试看不见它（只匹配 `else f"`，而这是跨行实参） | **已修**：新增平台**能力集合**（`CAPABILITY_NOTE_OCR` / `CAPABILITY_AUDIO_ASR` / `CAPABILITY_SUBTITLE`），该分支先过 `platform_supports(..., CAPABILITY_AUDIO_ASR)` 门禁，地址改由 `build_canonical_url` 构造；拿不到就**大声失败**，不再伪造 |
| **M2** 10 处独立 duration 字面量 | MEDIUM | `knowledge_service.py:149,152,713,719,727,732,737,746,755,764`。WS3 用一个 `duration=-5` 的行复现：列表算图文（`note_count=1`）、`start_sync(note)` 说"没有待入库的图文笔记"、统计算 0 —— 三条口径互相矛盾 | **已修**：全部改走 `content_kind.note_predicate` / `video_predicate`；新增守卫测试禁止手写字面量回归 |
| **L1** `item.platform or "douyin"` 洗白空平台 | LOW-MED | `ContentItem.platform` 是 NOT NULL 但**无 CHECK 约束**，`''` 可存；`or` 把它变成 `"douyin"` 从而通过预检。WS3 复现：`platform=""` 的行跑完 download 1 + ASR 1 + embedding 1 + upsert 1，`status='done'`，URL 是伪造的抖音地址 | **已修**：原样使用 `item.platform`，让 `""` 按"未登记平台"被预检拒绝 |
| **L3** `_collection_for` 裸 `KeyError` | LOW | 用会 `strip().lower()` 的注册表做校验，却拿**原始字符串**索引 `self._collections`；`'DoUyIn'` 抛 `KeyError`，而历史实现对非规范拼写一律抛 `ValueError` | **已修**：索引与 `_target_platforms` 都改用 canonical id |
| L2 抖音适配器仍持 10/20 阈值副本 | LOW | `adapters/douyin.py:124,126,140`；未纳入 `substantive.py` 清单，也在漂移守卫之外 | **接受**：该层零调用者（见 §2.1），属惰性代码。**§2.1 一旦选 B（接管）就必须先清掉它** |
| L4 `has_substantive_content` 一值五写零读 | LOW | `save_state` 计算两态；另有 4 处重置路径直接写 `False`（都同时清空 `transcript_text`，因此取值一致）。**全仓库无任何读者** | **接受并记录**：现在取值是真的，但没人读。退役需要迁移，见 §2.4 |
| L5 图片域名依赖导入顺序 | LOW | `import app.core.external_urls` 后表为空；`app.main` 后齐备。WS3 穷举了树内入口，**未找到**会在空表下到达 `safe_platform_image_url` 的路径 | **接受**：语义 fail-closed（空表 = 拒绝一切），且有漂移守卫。属长期债 |
| L6 原始 SQL 插入仍吃旧默认值 | LOW | 升级库上 `DEFAULT 'whisper_asr'` 仍在；ORM 会显式写入 `''`，但绕过 ORM 的裸插入会拿到旧默认值。无实际站点 | **接受并记录**：`entities.py` 的保证措辞应收紧到"ORM 路径" |

> **元教训（值得单独记住）**：三个实现者各自都在报告里写了"已完成"，而**三条最关键的声明是不成立的**。发现它们的唯一原因是有一个**只读、无写入权、且被明确要求不相信同伴**的第四方。这个角色不能由实现者自己兼任——这正是本版坚持 WS3/WS4 只读的原因。

### 1.5 第二轮复验（WS4）：修复引入的新问题

WS3 只读复验之后，Lead 又做了 4 处收尾修复。第五方（WS4，同样只读）对**这些收尾改动**再做一次对抗性复验，**又找出 4 个问题——其中包括收尾修复自己引入的一个新的静默路径**。证据见 `docs/audits/WS4-REVERIFY.md`（468 行）。

| 发现 | 严重度 | 说明 | 处置 |
|---|---|---|---|
| **L3-a** 规范化只覆盖集合、不覆盖元数据 | MEDIUM-LOW | 收尾把**集合查找**改成 canonical id，但写进 `chunk_id` / `metadata["platform"]` 的仍是原始拼写。于是 `platform='DoUyIn'` 的向量落进 `akasha_douyin`，而 `search()` 按 metadata 平台过滤时把它们全部丢弃：**入库显示 done，检索永远 0 命中**。修复前这里是响亮的 `KeyError`，所以这是**修复引入的回归**（由响亮失败变成静默失败） | **已修**：在写入前就把 `platform` 规范化为 `get_platform(...).platform`；`clear_platform` 同样规范化（否则会建出 `akasha_DoUyIn` 垃圾分区 + 过期句柄） |
| **M1-b** 能力事实有两个归属 | MEDIUM-LOW | `CAPABILITY_NOTE_OCR` 与 `note_extraction` 布尔量并存：声明了能力却忘了置布尔量的平台，会被静默改道去下载音频做 ASR | **已修**：`note_extraction` 改为**由能力集合派生的属性**，该事实只剩一处声明 |
| **M1-a** 音频地址优先信数据库列 | MEDIUM-LOW | 收尾写成 `canonical_url or build_canonical_url(...)`，把数据库列置于注册表之上。应用内所有写入者都是注册表派生的，所以只有脏数据/遗留数据能注入外部主机 | **已修**：改为 `build_canonical_url(...) or canonical_url`，注册表优先 |
| **L3-b** `clear_platform` 仍用原始键 | LOW | `self._collections[platform]` 未规范化 | **已修**（与 L3-a 同一处） |

**守卫测试承重性（这一点比缺陷本身更重要）**：WS4 指出 Lead 首版的守卫测试里有 **1 条完全不承重**、1 条名不副实——`test_audio_asr_path_is_capability_gated_not_a_fallback` 在修复被完全撤销后**仍然通过**（它只 grep 了 import 字符串），`test_note_and_video_predicates_are_exact_complements` 把谓词改成 `column > -1` 也照样通过（它比对的是编译后的 SQL 字符串快照，不是性质）。

Lead 随后做了两件事：
1. 把这两条**重写为行为测试**：新增 `tests/test_pipeline_platform_source.py`，真的建库、插行、跑 `_run_sync`，断言"抖音视频走注册表地址并完成"、"只声明 OCR 的平台命中图集 OCR 且**绝不**下音频"、"未登记/空平台在**任何付费调用之前**失败"；谓词测试改为真建表计数，断言 `note + video == total`。
2. 对自己新写的守卫做**突变测试**（撤销修复 → 断言测试必须变红）：

```
mutation                                   expected  observed  verdict
M1-douyin-audio-asr-capability-removed     FAIL      FAIL      OK
M1b-note-extraction-not-derived            FAIL      FAIL      OK
L1-platform-laundering-restored            FAIL      FAIL      OK
M2-note-predicate-broken                   FAIL      FAIL      OK
M1-b-hardcoded-url-restored                FAIL      FAIL      OK
ALL MUTATIONS CAUGHT
```

> **这是本轮最该带走的方法**：一个"守卫测试"若不证明它会在修复被撤销时失败，它就只是文档。首版守卫有 1/5 不承重，而**发现它的方式是把修复真的撤掉再跑一遍**——不是读一遍测试代码。

---

## 2. 未决项：需要先决策，不得直接开工

以下每一项都是**真实的开放问题**。本版**不给出工期**，因为在决议达成前任何数字都是编的。

### 2.1 适配器层：接管还是退役？（**必须二选一**）

**事实**：`adapters/` 有 132 行的 `BasePlatformAdapter` ABC 和两个实现，但 `get_adapter()` **没有任何生产调用者**；真实入库走的是 `knowledge_service.py:321-427` 的 `if/elif/else` 分支加直连 `bilibili_content_fetcher`。

因此仓库里**同时存在两套阈值**：适配器层 10 字符，真实 B 站路径 50 字符（`bilibili/content_fetcher.py:29`）。

**这不是技术问题，是治理问题。** 三个选项：

| 选项 | 含义 | 代价 |
|---|---|---|
| **A. 退役** | 删除 `adapters/`，`knowledge_service` 分支为唯一 owner | 删掉一个已写好但未接线的抽象；未来加平台需要重构 |
| **B. 接管** | 把 `knowledge_service` 分支改为调用适配器，适配器层成为唯一 owner | 需要把 50 字符阈值、分P、取消语义、检查点续传全部搬进适配器契约——这是**大手术**，且 `fetch_item_content() -> str` 现有签名承载不了分P与来源信息 |
| **C. 维持** | 两套并存 | **已被否决**：两套阈值就是当前最该修的债 |

> **本文档不替你选。** 但在选定之前，**任何新平台都不应开工**——因为新平台会决定这个选择的结果，而不是反过来。

### 2.2 下一个平台：先做产品取舍，不是技术选型

v3 的"外部 API 实测结果"表**本身已损坏**（原文含"已空降""可名解析""需要浏览器发纯"等乱码），且把 **HTTP 403 标注为 ✅ 可用**——那是"未登录无法查看更多内容"，不是"属性解析问题"。

**因此本版不转述任何未经验证的外部 API 结论。** 需要的是先回答：

```
问题 1：这 5 个平台里，哪 2 个覆盖你 80% 的实际收藏？
问题 2：如果平台 A 明天失效，你接受"该平台内容暂时不可同步"，
        还是要求必须有人工导出兜底？
问题 3：你愿意为平台 B 每 3 个月一次的签名维护付费吗（时间成本）？
```

**在问题 1 有答案前，平台优先级表是空谈。**

### 2.3 外部事实：唯一可确认的结论

- **`Binaryify/NeteaseCloudMusicApi`（网易云，约 30k stars）已于 2024-02 归档且仓库清空**。任何依赖它的方案已失效。
- 其余平台（知乎、小红书、YouTube、豆瓣）的"隐藏 API"现状**本版不做断言**，因为未在本环境重新实测。若需要，应作为一个**独立取证任务**执行，产出带时间戳的原始响应，而不是写成结论。

### 2.4 `has_substantive_content`：修复还是退役？

若 §1.2 第 6 条选择了"给唯一写入者"，则它成为一个可信的冗余门禁；若选择退役，需要同时确认**无任何读者**。**这是一个需要显式确认的决定**，因为它是数据库列，退役需要迁移。

### 2.5 已明确**不做**的事（附理由）

| 不做 | 理由 |
|---|---|
| 新增 `content_subtype` / `language` / `has_watermark` / `subtitle_url` 列 | v3 的 `ALTER TABLE` 块（v3:712-733）被标注为"修复暗病"，但暗病并不存在。这些字段**没有任何消费者**，加列即死代码。`content_kind` 已存在（`migration.py:259`、`entities.py:113`） |
| 新增 `ContentItem` Pydantic 模型 | `ContentItem` **已是** ORM 实体（`entities.py:95`），同名遮蔽是自找的混乱 |
| 新增 5 个异常类到 `core/exceptions.py` | 该模块**不存在**；`error_code` 列存在但流水线从不写它 |
| 并发信号量塞进 `wbi.py` | `wbi.py:39-42` 的注释**明确说明**它正是为规避跨事件循环 Semaphore 而设计。往里加 `asyncio.Semaphore` 是反模式 |
| 水印裁剪（裁掉图片下沿 20%） | 小红书图文正文常在图片下部。裁剪会**破坏 OCR 输入**，且 v3 的 `cropped.save(image_path)` 是**原地覆盖、不可逆** |
| 关键词删段式广告过滤 | v3 的 `p.decompose()` 会删掉任何含"广告/推广/赞助"的真实正文（例如"本文不含任何广告"），且 `.AdblockBanner` 是编造的类名 |

---

## 3. 前置门禁（在任何新平台开工之前）

这些不是"建议"，是**开工条件**：

- [ ] **G1**：§2.1 的 A/B/C 已选定，且适配器层与 `knowledge_service` 分支**只剩一个 owner**
- [ ] **G2**：§2.2 问题 1 已回答（选定 2 个平台）
- [ ] **G3**：A1–A7 全部通过（当前两个平台先修稳）
- [ ] **G4**：前端工作量已纳入估算。以下均为**实测**（`Select-String` 计数 + `node` 解析），不是估计：

  | 位置 | 事实 | 加平台时的后果 |
  |---|---|---|
  | `frontend/src/api.ts:29` | `PlatformKind = 'douyin' \| 'bilibili'`（联合类型） | 新平台字符串**编译期类型错误** |
  | `frontend/package.json:11` | `build` = `node scripts/check-i18n.mjs && node scripts/check-theme-contrast.mjs && tsc -b && vite build` | i18n 检查是**构建的第一个门禁**，失败即 `process.exit(2)`，构建中止 |
  | `frontend/src/i18n.tsx` | **8 个语言块**（zh/en/ja/fr/de/ko/ru/hi）；基线实测 `node scripts/check-i18n.mjs` → `OK — 8 languages, 400 keys each; 334 static t() keys all defined`，exit 0 | 新增平台文案必须 **8 语言 × key 完全对齐**，否则构建失败 |
  | `frontend/src/components/LoginModal.tsx` | **80** 处平台引用；`:281`/`:298`/`:382` 硬编码 `/platform-icons/{douyin,bilibili}.svg` | 登录 UI 只有两个平台的位置 |
  | `frontend/src/components/SettingsModal.tsx` | **28** 处；`:80-81` 三元兜底 `else douyin.svg` | 新平台显示抖音图标 |
  | `frontend/src/components/SourcesPanel.tsx` | **21** 处；`:921`/`:933` 硬编码两个图标 | 来源面板同上 |
  | `frontend/src/components/Workspace.tsx:230` | `platform === 'bilibili' ? bilibili.svg : douyin.svg` | 三元兜底，新平台显示抖音图标 |
  | `frontend/src/api.ts` | **20** 处平台引用 | — |
  | `frontend/src/store/workspace.ts:12` | 平台状态字段 | — |
  | `frontend/src/i18n.tsx:214` | `footerText: 'Akasha-RAG © 2026 · 基于抖音收藏夹构建 · AI 驱动'` | 加平台后该文案在 **8 种语言中全部变成谎言** |
  | 图标资源 | 实为 `public/platform-icons/*.svg`（**不在** `src/` 下） | 每新增平台需补一个图标资源 |

  > **v3 最严重的遗漏**：全文 787 行**零次提到前端**。平台的联合类型、8 语言文案门禁、品牌文案与图标，与后端是**同一批工作量**。只做后端，用户界面上根本选不到新平台。
- [ ] **G5**：维护成本已书面接受（签名/反爬的更新频率是**永久订阅**，不是一次性集成）

---

## 4. 第一性原理

```
First Principle
  这是一个本机个人 RAG。不可约目标：
  把"我自己收藏的、我有权访问的内容"变成可检索、可问答的知识。
  平台是手段，不是目标。

Non-negotiables
  N1. 实质性正文门禁不得降低（10/20/50）。这是防止标题污染向量库的唯一屏障。
  N2. 436 个测试 + 前端 typecheck + i18n 构建门禁必须保持绿。
  N3. 用户凭证只走 secure_storage（core/secure_storage.py），不落明文 json。
  N4. 抓取对象是用户自己的收藏，不是公开爬取。

Assumptions to Drop
  ✗ "加一个平台 = 一次性集成"
     → 它是永久维护订阅。签名/反爬会持续失效。
  ✗ "后端完成 = 接入完成"
     → 前端联合类型 + 8 语言文案 + 品牌文案是同一批工作量。
  ✗ "适配器层已存在，可直接扩展"
     → 实测零调用者，且其 ABC 的抽象方法集与计划书写的契约不一致。
  ✗ "文档字数 = 实施进度"
     → v3 用 787 行描述一个不存在的代码库。文字量不是证据。

Smallest Sufficient Path
  1. 平台事实收敛为注册表（一个 owner）              ← 已实施
  2. note/video 判定收敛为唯一求值器                  ← 已实施
  3. 能力预检前移到付费阶段之前                       ← 已实施
  4. 消除静默抖音兜底（URL / collection / 默认参数）    ← 已实施
  5. 然后才谈"下一个平台是谁"                         ← 待人决策（§2.2）

Escalation Signal
  真正的取舍不是"选哪个平台"，而是"哪 2 个平台覆盖 80% 需求"。
  在 G1/G2 解决前，任何工期数字（29 周 / 10-15 周 / 2-3 周）
  都是不可核验的——v3 给了三个互相矛盾的估算，且都无依据。
```

---

## 附录 A：v3 逐条勘误

> 保留此表的目的：**防止同一批错误被再次引入**。

| v3 位置 | v3 声称 | 实测事实 | 性质 |
|---|---|---|---|
| v3:44 | `adapters/base.py` + `factory.py` 已存在，可保持一致 | 文件确实存在，但 `get_adapter` **零生产调用者**（见 D1）。该层是**未接线的第二套系统** | 半真 |
| v3:88 | "通过 docstring 第 4 行名称点注册" | **全仓库无任何代码读取 `__doc__` 或做注册**。注册逻辑是 `factory.py:25-33` 的 `if/elif` | **虚构** |
| v3:57-82 | 新建 `ContentItem` Pydantic 模型 | `ContentItem` 已是 ORM 实体（`entities.py:95`） | 命名冲突 |
| v3:98-115 | 契约含 `login_with_qr`/`get_user_favorites`/`download_media` | 真实 ABC 是 `check_auth`/`get_qr_code`/`check_qr_status`/`fetch_collections`/`fetch_collection_items`/`fetch_item_content`（`base.py:87-131`）。`download_media` **不存在** | **虚构** |
| v3:108 | `fetch_item_content(item_id) -> ContentItem` | 真实签名 `fetch_item_content(remote_item_id, part_id=0, item_meta=None) -> str`（`base.py:122-132`） | 契约不符 |
| v3:138 | `content_kind = "video" if duration else "note" if platform=="douyin" else "article"` | 现行规则**与平台无关**（`favorites_service.py:477`）。该式会让抖音图文变 `article`、知乎视频变 `note` | **方向相反** |
| v3:147 | `ALTER TABLE ... IF NOT EXISTS` 是 SQLite 错误语法 | **正确**（实测 `OperationalError: near "EXISTS"`） | ✅ 唯一正确的技术判断 |
| v3:164-170 | `_IMAGE_DOMAINS` 应为**集合** | 真实结构是 `dict[str, frozenset]`（`external_urls.py:12`），被 `.get(platform)` 消费。改成 set 会 `AttributeError` | **会崩** |
| v3:175 | `SUPPORTED_PLATFORMS = ("douyin", "bilibili11")` | 真实值是 `("douyin", "bilibili")`；`bilibili11` 不存在 | 抄写错误 |
| v3:189 | "4 处 `!= False` 判断" | 全仓库**只有 1 处** `has_substantive_content` 读取，且赋值而非判断。且**从未写入 `True`** | 计数错误 + 误判根因 |
| v3:206 | `if item.transcript_source in ("youtube_subtitle", ...)` | 写入点**硬编码** `"whisper_asr"`，该值**永不可能出现** → 分支永不执行 | **死分支** |
| v3:219-227 | 信号量写入 `wbi.py:39-42` | 该文件注释明确说明其设计正是为规避跨 loop Semaphore | **反模式** |
| v3:247 | 知乎 `/api/v4/favlists` "✅ 可用" | 未在本环境复测。**本版不背书** | 未经验证 |
| v3:249 | 网易云"已空降" | 原文乱码（应为"已归档"）。`Binaryify/NeteaseCloudMusicApi` 确实已归档清空 | 结论对、文本坏 |
| v3:251 | 豆瓣"✅ 可用（实测 HTTP 403 = data-id 是数字属性）" | **403 是"未登录无法查看更多内容"**，不是属性解析问题。把 403 标为可用是最危险的一类错误 | **因果颠倒** |
| v3:236-239 | 未定义函数改为 `raise NotImplementedError` | 这是把"缺失实现"变成"运行时崩溃"，不是修复 | 伪修复 |
| v3:685-704 | 集成测试 `login_with_qr()` + 真实扫码 | `mark.integration` **未注册**（`pyproject.toml` 无 `markers=`），`tests/integration/` 不存在，CI 无 `-m` 过滤 → 需联网/凭证的测试**每次 push 全量执行** | **暗桩** |
| v3:743 | 发布检查项"广告过滤生效" | 过滤实现会删除含"广告"的真实正文；类名是编造的 | 验收给不存在的东西 |
| v3:751 | 发布检查项"图片水印去除成功" | 实现是裁掉下沿 20% + 原地覆盖，破坏 OCR 输入 | 验收给有害的东西 |
| v3:266-267 | "预有 436 个测试...必须保持" | 方向正确，但 **v3 未给出任何运行记录**，且加 4 平台后测试数必然 > 436，该条**永不可证伪** | 空头支票 |
| v3 全文 | 前端 | **零次提及**。而 `api.ts:29` 是联合类型、i18n 有 8 语言构建门禁 | **重大遗漏** |
| v3:19,272 | 29 周 → 10-15 周 → 每平台 2-3 周 | 三个互相矛盾且均无依据的估算 | 不可核验 |

**v3 中唯一值得保留的**：§2.2 的**产品判断方向**（砍掉网易云、按 Cookie 门槛排优先级、先做知乎+豆瓣）。该方向本版保留，但拒绝接受其未经验证的技术前提。

### A.1 本版自身的一次勘误（方法论示范）

上一轮口头审查中曾出现一句错误断言："`BasePlatformAdapter.__abstractmethods__` 为 `frozenset()`，ABC 已失效"。**该断言经实测推翻**：

```
$ python -c "from app.services.adapters.base import BasePlatformAdapter as B; print(B.__abstractmethods__)"
frozenset({'check_auth', 'fetch_collections', 'fetch_collection_items',
           'platform_name', 'check_qr_status', 'fetch_item_content', 'get_qr_code'})
```

**ABC 是健全的**，7 个抽象方法全部生效。真正的问题是另一件事：`download_media` 与 `login_with_qr` **在基类上根本不存在**（`hasattr` 均为 `False`），所以 v3 写的那套"抽象契约"是它自己虚构的——适配器按 v3 实现会直接 `TypeError`，而不是"ABC 失效"。

**保留此条的目的**：证明规则 R1/R2 对**本文档的作者**同样适用。任何未经命令验证的断言，包括来自审查者的，都不得写入结论。

---

## 附录 B：本轮修复的实施记录

> 由 WS1 / WS2 / WS3 三个工作流执行；WS3 为独立对抗性验证，其结论见 `docs/audits/WS-VERIFICATION.md`。

| 工作流 | 范围 | 任务 |
|---|---|---|
| WS1 | 平台注册表 + canonical_url / content_kind 收敛 | task-1 |
| WS2 | 入库门禁：transcript_source、死列、能力预检 | task-2 |
| WS3 | 独立对抗性验证（不修代码，只出证据） | task-3 |

**报告纪律**：任何"已修复"的声明必须附**命令与输出**，不接受"代码看起来对了"。WS3 有权推翻 WS1/WS2 的任何结论。
