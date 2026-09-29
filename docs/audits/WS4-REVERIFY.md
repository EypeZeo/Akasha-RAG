# WS4 — Adversarial Re-Verification of the Four Post-WS3 Fixes

**Verdict: all four fixes are real and demonstrably load-bearing (each one was reverted in a
scratch copy and the failure was reproduced). Two of the four are incomplete in adjacent
places, one new silent path was introduced by the L3 fix, one of the 23 new guard tests is
non-load-bearing, and one guard test does not check the property in its own name. No blocking
finding: no paid stage is reachable from an unsupported platform, no canonical-data loss, no
weakened security boundary.**

| | |
|---|---|
| Verified revision | `HEAD = f507af52be0d6690354f18286e947de3a8232ebb` + uncommitted WS1–WS4 worktree |
| File fingerprints (SHA256/16) | `knowledge_service.py 8D29C1906E62C22C` · `chroma_service.py 471FB78A70654A53` · `platform_registry.py 168954562AFBBD6E` · `content_kind.py 09494290D557A1CA` · `test_platform_gate_regressions.py 749593176AFCEC36` |
| Lead's last writes | `knowledge_service.py 18:27:22` · `chroma_service.py 18:27:32` · `test_platform_gate_regressions.py 18:29:47` · roadmaps 18:31:56 — my scratch dir was created `18:33:16`, after all of them |
| Baseline suite | `cd backend; .venv\Scripts\python.exe -m pytest -q` → **`545 passed, 3 warnings, 7 subtests passed in 46.14s`** (exit 0) |
| Lint gate | `cd backend; uv run --with ruff ruff check --select E9,F63,F7,F82 app tests` → **`All checks passed!`** (exit 0) |
| Guard-test file | `tests/test_platform_gate_regressions.py` → **23 tests** collected and passing (verified in a scratch copy too: `23 passed in 0.93s`) |
| Read-only | No repo source file was written by me. My only write is this report; scratch work lives in `%TEMP%\ws4probe` and `%TEMP%\ws4mut` (a py-only copy of `backend/`). |

Corrected premise: the task text calls item 1 "M2" and item 2 "M1", while `WS-VERIFICATION.md`
labels the catch-all-`else` URL as **W1-A** and the duration literals as **W1-C**. I keep the
task's labels (M2 = duration convergence, M1 = capability gate) and map them to W1-C/W1-A.

---

## 1. Baseline — PASS

```
$ cd backend; .venv\Scripts\python.exe -m pytest -q
545 passed, 3 warnings, 7 subtests passed in 46.14s        (exit 0)
$ cd backend; uv run --with ruff ruff check --select E9,F63,F7,F82 app tests
All checks passed!                                          (exit 0)
```
Pre-WS3(4) baseline was 436 → 522 (WS3) → **545 (+23 = exactly the new guard file)**, no failures.

Observation, not a violation: `backend/app/storage/{douyinrag.db,chroma,export_tmp}` mtimes moved
at `18:31:13–18:31:39` (the full-suite window; the WS3 report ran the same command). That predates
my scratch directory (`%TEMP%\ws4probe` created `18:33:16`, first probe module import `18:34:55`),
and every probe uses a temporary SQLite engine plus stubbed
`get_chroma_service`/`download_audio` and never constructs a real `ChromaService`, so no probe of
mine touched repo storage.

---

## 2. Item 1 (M2, WS3 finding W1-C) — **PASS**, with one guard-coverage gap

**2.1 Every former literal site now delegates.** The 10 sites WS3 listed
(`knowledge_service.py:149,152,713,719,727,732,737,746,755,764`) are now:

| former site | now |
|---|---|
| start_sync video filter | `knowledge_service.py:156` `video_predicate(ContentItem.duration)` |
| start_sync note filter | `knowledge_service.py:158` `note_predicate(ContentItem.duration)` |
| `total_video` / `total_note` | `:740` / `:746` |
| `v_pending`/`v_done`/`v_failed` | `:754` / `:759` / `:764` |
| `n_pending`/`n_done`/`n_failed` | `:773` / `:782` / `:791` |
| list `count_stats` video/note | `:927` / `:928` |
| list `items_query` video/note | `:939` / `:942` |
| `item_type` | `:968` `content_kind_for(item_platform, r.duration)` |

Independent grep of `backend/app` for `duration == 0` / `duration <= 0` / `duration > 0` /
`duration.is_(` returns **only** the definitions/comments in `content_kind.py`, one residual in
`adapters/douyin.py:92` (see 2.5) and two `duration or 0` normalisations
(`favorites_service.py:450`, `knowledge_service.py:967`). Nothing hand-written remains in a
`knowledge_service` branch predicate.

Call-level proof that the sites really go through the shared helpers — spies wrapped over
`ks.note_predicate` / `ks.video_predicate` (`%TEMP%\ws4probe\probe_m2_duration.py`):

```
predicate calls during list path: {'note': 4, 'video': 4}
predicate calls during stats path: note=4 video=4        # total_note,n_pending,n_done,n_failed
```

**2.2 The original WS3 reproduction, before and after.** One row: `platform='douyin'`,
`duration=-5`, `status='pending'`. "Before" = the same file with the 10 sites reverted to the old
literals in `%TEMP%\ws4mut\backend` (`%TEMP%\ws4mut\followup.py`):

```
BEFORE (M2 reverted):
  list_pending_items(content_type='note')  -> total=0 note_count=0 items=[]
  list_pending_items(content_type='all')   -> total=1 video_count=0 note_count=0 items=[('neg-1', -5, 'note')]
  stats                                    -> total_video=0 total_note=0
  start_sync(content_type='note')          -> pending_count=0 message='没有待入库的图文笔记，请先同步收藏夹'
AFTER (current revision):
  list_pending_items(content_type='note')  -> total=1 note_count=1 items=[('neg-1', -5, 'note')]
  list_pending_items(content_type='all')   -> total=1 video_count=0 note_count=1 items=[('neg-1', -5, 'note')]
  stats                                    -> total_video=0 total_note=1
  start_sync(content_type='note')          -> pending_count=1 message='已提交入库任务，共 1 个图文笔记'
```
Exactly WS3's contradiction, reproduced and closed: the row is now 图文 in the list, in
`start_sync(note)` **and** in the counters.

On a 4-row fixture (`-5, 0, NULL-assigned, 20`) the pre-fix literal expressions leave the
negative row in **neither** bucket — `old_note=2, old_video=1, total=4` — while the shared
predicates give `note=3, video=1, total=4`.

**2.3 `video + note == total` — PASS.** Per-value, against a real SQLite engine
(`probe_m2_duration.py`): `-100/-5/-1/0/1/20/3600` → `partition_ok=True, consistent=True` for
every value. NULL cannot be stored on a `Base.metadata.create_all` schema
(`duration INTEGER NOT NULL`; the ORM maps `duration=None` → `0`), so I proved the NULL case two
other ways:

```
raw SQLite: (NULL <= 0) OR (NULL IS NULL) = 1 ;  (NULL > 0) = None ;  NOT (NULL > 0) = None
nullable derived table WITH vals(d) AS (VALUES (NULL),(-100),(-5),(-1),(0),(1),(20),(3600)):
  note=5 video=3 total=8 partition_ok=True
```
Precision caveat for the record: as SQL *boolean* expressions the two are **not** literal
complements under three-valued logic (`NOT video_predicate(NULL)` is `NULL`, not TRUE, while
`note_predicate(NULL)` is TRUE). The partition holds because every counting site uses the two
positive predicates (`WHERE …`, `case((pred, 1), else_=0)`), never a negation — there is no
`NOT video_predicate` anywhere in `app/`. `content_kind.py:55`'s "精确补集" is true for row
partitioning, not for negation.

**2.4 Intended behaviour change worth knowing.** Because `is_note` is now
`needs_note_extraction(...)`, a **negative-duration douyin row moves from the audio-ASR branch to
the image-OCR branch** (`probe_m2_branch.py`):

```
duration=-5  -> note_ocr calls=1 download_audio=0 asr=0  status='failed' msg='未能提取到图文正文内容…'
duration=0   -> note_ocr calls=1 download_audio=0 asr=0
duration=20  -> note_ocr calls=0 download_audio=1 asr=1  status='done'
```
That is the point of the convergence (list/stats/sync already said 图文), but it is a routing
change, not a pure refactor: a legacy `duration=-1` douyin video is now OCR'd instead of
transcribed, and will typically fail with "未能提取到图文正文内容（已拒绝仅标题入库）".

**2.5 Residual (outside the 10 sites, out of scope but relevant to "one rule").**
`adapters/douyin.py:92` still hand-rolls `dur == 0 or dur is None` and `:102` the
`"note" if is_note else "video"` ternary. The adapter layer is **dead code** — repo-wide,
`fetch_item_content(` has only its three definitions and `get_adapter(` only its definition
(`adapters/factory.py:17`), so this cannot misroute today; it is the W2-A finding, unchanged.

---

## 3. Item 2 (M1, WS3 finding W1-A) — **PASS on all four sub-claims**, two new findings

**3.1 The critical regression check: douyin video ingestion still works.** End-to-end through
the real `_run_sync` with stubbed download/ASR/embedding/chroma
(`probe_m1_capability.py`, case A):

```
A) douyin VIDEO, canonical_url empty
[A] status='done' error_code=None
    download_audio calls: 1 ['https://www.douyin.com/video/dy-video-1']
    ASR calls: 1   embedding calls: 1   chroma.upsert calls: 1
      [{'platform_item_id': 'dy-video-1', 'platform': 'douyin', 'canonical_url': ''}]
    _run_sync outcome: no-exception
```
The doubly-covered-path concern is unfounded in the final version: douyin declares
`capabilities={CAPABILITY_NOTE_OCR, CAPABILITY_AUDIO_ASR}` (`platform_registry.py:103`). I also
confirmed the **intermediate single-valued variant would have broken it** — in the scratch copy,
douyin with `{CAPABILITY_NOTE_OCR}` only:

```
[A] status='failed'
    error_message="平台 'douyin' 未声明音频 ASR 提取能力（当前能力: ['note_ocr']）；拒绝回退到抖音地址"
    download_audio calls: 0 []   => "Douyin video ingestion REGRESSED"
```
and `test_registered_platforms_declare_their_real_capabilities` fails on that variant
(mutation 6 below). Controls: douyin note (`duration=0`) still takes OCR with no paid audio stage;
bilibili (`duration=60`, one part, stubbed fetcher) reaches the bilibili branch and finishes
`done` with `fetch_transcript` called once, i.e. it never touches the new gate.

**3.2 A third platform now fails loudly instead of downloading a fabricated douyin URL.**
`zhihu` monkeypatched into `PLATFORMS` (subtitle capability only):

```
platform_capability_precheck('zhihu') -> None   (registered: the precheck does NOT stop it)
[E] status='failed'
    error_message="平台 'zhihu' 未声明音频 ASR 提取能力（当前能力: ['subtitle']）；拒绝回退到抖音地址"
    download_audio calls: 0 []   ASR calls: 0   embedding calls: 0   chroma.upsert calls: 0
```
With the gate reverted in the scratch copy the same row **downloads
`https://www.douyin.com/video/answer-42` and finishes `done`** — the W1-A landmine, live.

**3.3 Both new `RuntimeError`s are reachable.** The second one needs a registered platform that
declares `audio_asr` but has no `canonical_url_template` and an empty DB `canonical_url`
(`probe_m1_capability.py`, case F): `error_message="平台 'foo' 缺少 canonical_url_template，
无法构造音频来源地址"`, status `failed`, **0 downloads**. Case G shows the positive path: the
same fake platform with a template downloads `https://foo.example/watch/foo-2` — the URL really
does come from the registry, not from a hardcoded host.

**3.4 Finding M1-a (MEDIUM-LOW, new trust in a DB column).** The audio URL is now
`canonical_url or build_canonical_url(...)` (`knowledge_service.py:486`) — the **DB row wins over
the registry**, whereas the pre-fix code ignored the column and always used the canonical douyin
template. Case B proves it:

```
[B] douyin VIDEO with canonical_url='https://www.douyin.com/video/DRIFTED-ID'
    download_audio calls: 1 ['https://www.douyin.com/video/DRIFTED-ID']   status='done'
```
Risk is bounded today: every in-app writer of the column is registry-derived —
`favorites_service.py:480,485 build_canonical_url(platform, remote_id)` and the
`IngestionItem.before_insert` hook at `entities.py:356-358` (`build_canonical_url(facts.platform,
…)`, which *raises* for an unregistered platform) — with one legacy exception, the one-off
douyin-only backfill `migration.py:352` (`'https://www.douyin.com/video/' || platform_item_id`).
The adapter DTOs (`adapters/douyin.py:101`, `adapters/bilibili.py:85`) never reach the column
(that layer has no callers). A foreign host therefore requires hand-edited/pre-v0.7 data or a
future writer. Direction to close: resolve the audio source from the registry first
(`build_canonical_url(...) or canonical_url`), or validate the chosen URL against the platform's
declared domains before handing it to the downloader.

**3.5 Finding M1-b (MEDIUM-LOW, capability set half-wired).** `CAPABILITY_NOTE_OCR` and
`CAPABILITY_SUBTITLE` are **declared but never read**: a repo-wide grep finds them only at their
definitions and the two `PLATFORMS` entries (`platform_registry.py:42,44,103,111`). The OCR
branch is still gated by the older boolean `PlatformFacts.note_extraction`
(`platform_registry.py:189`), and the subtitle branch by the literal
`item_platform == "bilibili"` (`knowledge_service.py:404`). Proven consequence
(`probe_m1_cap_consistency.py`):

```
A) note_extraction=False, capabilities={note_ocr, audio_asr}, duration=0
   needs_note_extraction -> False
   OCR branch ran: 0 calls | ASR branch ran: download=1 asr=1 ['https://www.zhihu.com/answer/answer-1']
   status='done'          => note_ocr declared, OCR not taken; the answer page was ASR'd
B) note_extraction=True, capabilities=set(), duration=0
   OCR branch ran: 1 calls | ASR branch ran: download=0 asr=0     => ran OCR with no declared capability
```
So `note_extraction` and `CAPABILITY_NOTE_OCR` are two owners of one fact with no equivalence
guard, and the new capability member cannot influence the branch it names. Same class of
"silent misroute for the next platform" that M1 was about, one layer over.

---

## 4. Item 3 (L1, WS3 finding W1-B) — **PASS**

`item_platform = item.platform` (`knowledge_service.py:325`, verbatim) and the precheck at
`:343`. For a row with `platform=""`, `duration=20`, `status='pending'`
(`probe_l1_empty_platform.py`):

```
[empty-video] platform='' duration=20
    status='failed' error_code='unsupported_platform'
    error_message="不支持的平台: ''，当前支持: douyin, bilibili"
    download_audio calls: 0 []   ASR calls: 0   embedding calls: 0   chroma.upsert calls: 0
    _run_sync outcome: RuntimeError: 入库处理结束：1/1 个内容失败…
```
Same for `'   '`, for `''` with `duration=0`, and for `'zhihu'` — **zero paid calls in all four
cases**. Reverted in the scratch copy (`item.platform or "douyin"`), the identical row reproduces
WS3's variant B byte-for-byte:

```
[empty-video] status='done' error_code=None
    download_audio calls: 1 ['https://www.douyin.com/video/r-empty-video']
    ASR calls: 1   embedding calls: 1   chroma.upsert calls: 1
    FAILURES: ['empty-video: PAID STAGES REACHED (1, 1, 1, 1)', "status='done' (expected failed)",
               'fabricated douyin URL used']
```

**No other laundering path reaches a paid stage.** Repo-wide grep of `backend/app`:

* `platform or "douyin"` / `or 'douyin'` — **zero** remaining.
* `getattr(x, "platform", "douyin")` — 4 sites: `favorites_service.py:86,364,415` and
  `knowledge_service.py:960`. All dormant, and none of them launders `""` anyway —
  `getattr(SimpleNamespace(platform=""), "platform", "douyin") == ''` (verified): the default only
  fires when the attribute is *absent*. `FavoriteScrapeSnapshot.platform` is a real dataclass field
  (`douyin_collector.py:105`, default `"douyin"`), and the list-path `Row` always selects
  `ContentItem.platform` (`knowledge_service.py:877`), so the fallback is unreachable in-tree.
* `platform: str = "douyin"` parameter defaults — only the douyin collector's own snapshot field
  and the (now-removed, test-pinned) `upsert_video_chunks` default.

An empty string that does get written to `ContentItem.platform` (e.g. a hand-built
`FavoriteScrapeSnapshot(platform="")`) propagates verbatim into the DB and is now rejected by the
precheck — which is the intended behaviour.

---

## 5. Item 4 (L3, WS3 finding W1-E) — **PASS for the stated contract; incomplete, one new silent path**

**5.1 The four spellings resolve; unknown platforms stay `ValueError`.**
`_collection_for` now indexes `self._collections[facts.platform]`
(`chroma_service.py:60-64`) and `_target_platforms` returns the canonical id (`:70`)
(`probe_l3_chroma.py`):

```
'DoUyIn' -> 'akasha_douyin'   'DOUYIN' -> 'akasha_douyin'   ' douyin ' -> 'akasha_douyin'
'BiliBili' -> 'akasha_bilibili'   'bilibili' -> 'akasha_bilibili'   'douyin' -> 'akasha_douyin'
'zhihu' | '' | '   ' | None | 42 -> UnsupportedPlatformError(ValueError subclass=True)
_target_platforms('DoUyIn') -> ('douyin',)   _target_platforms(None) -> ('douyin','bilibili')
search(platform='DoUyIn')  -> [('douyin', '1')]
search(platform=' douyin ', scope_ids={('douyin','1')}) -> [('douyin', '1')]
```
`except ValueError` callers are unaffected: `api/routes/knowledge.py:186` still converts it to
`{"success": False, "message": …}` (HEAD raised `ValueError` on the identical path — no
regression, matching WS3 §4.4).

**5.2 Downstream metadata comparisons — no break found.** There are exactly two places where a
platform string is compared against stored/derived platform data:

* `chroma_service.py:150-151` (`source_platform != target`), where `target` now comes from
  `_target_platforms` in canonical form;
* `rag_service.py:250` (`(_hit_platform(h), h["platform_item_id"]) in scope_ids`, plus the
  `source_key`/lookup uses at `:735` and `:898`), where the hit's platform originates from the
  same vector metadata and `scope_ids` is built from `ContentItem.platform`
  (`rag_service.py:267-277`), with `"all"`/`""` normalised to `None` at `:699`. `_hit_platform`
  itself returns `""` for a missing platform (`:38-45`) — no douyin default there.

Both sides are canonical for every in-tree writer, so `_target_platforms("DoUyIn")` returning
`('douyin',)` can only *add* matches relative to the previous `KeyError`; it cannot drop a
candidate that used to be returned. The only way the two sides can disagree is a non-canonical
value *in the DB or in the metadata* — which is finding L3-a below.

**5.3 Finding L3-a (MEDIUM-LOW, NEW silent path introduced by the fix).** Canonicalisation is
applied to the **collection lookup** but *not* to what gets written into it. `upsert_video_chunks`
stamps the raw argument into metadata (`chroma_service.py:96 "platform": platform`) and into the
chunk-id prefix (`:89-92`). Probe (`probe_l3_metadata.py`):

```
DB platform='DoUyIn'
    chunk ids written    : ['DoUyIn:item-1:0:0']
    metadata['platform'] : 'DoUyIn'
    written into collection: 'akasha_douyin'
    search(platform=None)      : 0 hits []
    search(platform='DoUyIn')  : 0 hits []
    => ORPHANED: in akasha_douyin, filtered out at chroma_service.py:151
```
Before the L3 fix the same call raised (`KeyError: 'DoUyIn'` — reproduced in the L3-reverted
scratch copy), so ingestion failed **loudly** and the row was marked `failed`. Now the row can
finish `done` while its vectors are unreachable by every retrieval path: a
"successful ingestion that is invisible to RAG" is worse than the failure it replaced. Reachable
only with dirty data (no in-tree writer produces a non-canonical `ContentItem.platform`), but it
is the same class as the finding the fix set out to close. One-line direction: canonicalise on
write (`facts = get_platform(platform)` → use `facts.platform` for the metadata, chunk-id prefix
and log line, exactly as `_collection_for` now does).

**5.4 Finding L3-b (LOW, latent).** `clear_platform()` still indexes the raw string:
`self._collection_for(platform)` validates and discards, then
`self._collections[platform] = self._create_collection(platform)` (`chroma_service.py:224-227`).
`clear_platform('DoUyIn')` deletes the canonical `akasha_douyin` (via `_collection_name`) but
stores the recreated collection under key `'DoUyIn'`, leaving `_collections['douyin']` a stale
handle to a deleted collection:

```
deleted chroma collections : ['akasha_douyin']
_collections keys after    : ['DoUyIn', 'bilibili', 'douyin']
_collections['douyin']     : 'akasha_douyin'  (stale handle to the deleted collection)
```
Unreachable from the HTTP surface today (`ClearAllRequest.platform: PlatformFilter`,
`api/routes/knowledge.py:326`), so latent — but it is the same one-line miss the fix corrected two
methods above.

Also noted, not a regression: `delete_video`'s DB lookup still compares the **raw** query param
case-sensitively (`knowledge_service.py:1001-1002`), so `?platform=DoUyIn` returns
"内容 … 不存在" before Chroma is reached. No 500, no KeyError; the L3 change is layer-local by
design.

---

## 6. Review of `backend/tests/test_platform_gate_regressions.py` (23 tests)

Mutation harness: `%TEMP%\ws4mut\backend` is a `.py`-only copy of `backend/`; each mutation is
applied there, the guard file is run with the repo's venv, then the copy is restored
(`%TEMP%\ws4mut\mutate.py`, `mutate2.py`, `followup.py`). Pristine copy: `23 passed in 0.93s`.

| test | load-bearing? | evidence |
|---|---|---|
| `test_knowledge_service_has_no_independent_duration_literals` | **YES** | MUT-1: `1 failed, 22 passed` → this test |
| `test_note_and_video_predicates_are_exact_complements` | **PARTIAL** | MUT-8 (drop `IS NULL` arm) → `1 failed` = this test; but MUT-7 (`video_predicate` → `column > -1`) → **23 passed** while my probe shows `duration=0`/NULL counted by both and `video+note != total` |
| `test_duration_classification_agrees_across_every_consumer` (×7) | no (by construction) | pure `content_kind` helper test; cannot fail from any consumer regression |
| `test_pipeline_builds_no_hand_made_douyin_url` | **YES** | MUT-5 → failed |
| `test_douyin_url_sites_are_an_explicit_closed_set` | **YES** | MUT-5 → failed (both directions checked) |
| `test_audio_asr_path_is_capability_gated_not_a_fallback` | **NO — non-load-bearing** | MUT-5 (`2 failed, 21 passed`) — this test still **passed** after the gate was deleted and the hand-built URL restored, because it only asserts the *strings* `CAPABILITY_AUDIO_ASR` and `build_canonical_url` occur in the file, and both survive in the import block (`knowledge_service.py:36-41`) |
| `test_empty_platform_is_not_laundered_into_douyin` | **YES** | MUT-3 → failed |
| `test_unregistered_and_blank_platforms_are_rejected_by_capabilities` (×4) | no (by construction) | registry property test |
| `test_registered_platforms_declare_their_real_capabilities` | **YES** | MUT-6 (douyin → `{note_ocr}` only) → failed; pins the exact intermediate-variant regression |
| `test_mixed_case_platform_resolves_instead_of_raising_keyerror` (×4) | **YES** | MUT-4 → `4 failed` (all params) |
| `test_unknown_platform_still_raises_valueerror_not_keyerror` | no (for this fix) | MUT-4 → still passed (the reverted code raises before indexing); it fails only if all validation is removed |

**Demonstrated load-bearing: 9 fully + 1 partially (the complement test, which does pin the
`IS NULL` arm). Demonstrated non-load-bearing: 1
(`test_audio_asr_path_is_capability_gated_not_a_fallback`). Property-only, unfailable-by-this-fix:
12.** The required "pick at least 2 tests and revert the fix" bar is exceeded — four reverts
(M2, L1, L3, M1) were performed and each produced the expected failure, plus 3 additional
surviving mutations that expose gaps.

Two further guard-coverage gaps, both demonstrated:

* **MUT-2** — reverting only the note sites to a hand-written `ContentItem.duration <= 0`
  (semantically converged, no longer shared) → **23 passed**. The guard matches only
  `duration == 0` and `duration.is_(None)`; a re-diverged-but-equal predicate is invisible. This
  is a *convergence* gap, not a correctness gap (`<= 0` agrees with `note_predicate` for every
  storable value).
* **MUT-7** (above) — `test_note_and_video_predicates_are_exact_complements` is a compiled-SQL
  substring snapshot (`assert "duration <= " in …`), not a property test. My
  `probe_m2_duration.py` is the only artifact that actually asserts the partition property.

---

## 7. Findings, by severity

**MEDIUM-LOW**
1. **L3-a (new, silent)** — `chroma_service.py:89-96` vs `:54-64`: a non-canonical platform now
   writes vectors into the canonical collection while stamping `metadata['platform']` with the raw
   spelling, so `search()` (`:150-151`) drops them forever while ingestion reports `done`.
   Introduced by the L3 fix (before: `KeyError` → `failed`). Reproduced end-to-end with doubles.
2. **M1-b** — `CAPABILITY_NOTE_OCR`/`CAPABILITY_SUBTITLE` are declared but never read; the OCR
   branch is still gated by the legacy boolean `note_extraction`, so a platform that declares
   `note_ocr` without the boolean is silently routed into ASR (reproduced: a `zhihu` answer page
   downloaded and ASR'd to `status='done'`). Two owners for one fact, no equivalence guard.
3. **M1-a** — `knowledge_service.py:486` prefers the DB row's `canonical_url` over the registry
   when resolving the audio source; the pre-fix code ignored the column. Demonstrated with a
   drifted value; bounded because every in-app writer is registry-derived (§3.4).

**LOW**
4. **L3-b** — `clear_platform` (`chroma_service.py:224-227`) still indexes `self._collections`
   with the raw string: junk key + stale canonical handle. Unreachable from the API
   (`PlatformFilter`).
5. **Guard test non-load-bearing** — `test_audio_asr_path_is_capability_gated_not_a_fallback`
   passes with the fix fully reverted (import strings survive); it should assert behaviour
   (e.g. call `platform_supports` expectations or drive the pipeline) rather than file contents.
6. **Guard test over-named** — `test_note_and_video_predicates_are_exact_complements` does not
   check the complement property (MUT-7 survives 23/23); the partition check should be a real
   query over a value fixture, as in `probe_m2_duration.py`.
7. **M2 residual** — `adapters/douyin.py:92,102` still hand-roll the duration rule and the 10/20
   thresholds (dead code today: no callers of `fetch_item_content`/`get_adapter` in-tree);
   `favorites_service.py:450` / `knowledge_service.py:967` keep the benign `duration or 0`
   normalisation. The archived claim "one rule" is true for the live pipeline only.

**No blocking finding.** No paid stage reachable from an unsupported/blank platform, no canonical
data loss, no loosened gate, no weakened trust boundary, no test decrease (545 passed).

## 8. What I could NOT verify

1. **A true NULL `duration` row through the app.** `duration` is `INTEGER NOT NULL` on a schema
   built by `Base.metadata.create_all`, and assigning `None` through the ORM stores `0`. NULL
   semantics were therefore proven with raw SQLite/derived tables, not with an ORM row on a
   legacy nullable column.
2. **Reachability of findings L3-a / L3-b from the HTTP surface.** No in-tree writer produces a
   non-canonical `ContentItem.platform`, and the API platform parameters are `PlatformFilter`
   (`Literal["all","douyin","bilibili"]`). Both require dirty/legacy data or a future caller; I
   could not construct an HTTP path.
3. **Live end-to-end ingestion** (real Douyin/Bilibili, real DashScope ASR, real Chroma
   persistence) — no credentials/network by design; all evidence comes from the repo's own
   stubbed harness and `object.__new__` Chroma doubles. Real Chroma was never instantiated, so
   the on-disk store was not touched.
4. **Finding M1-a's real blast radius** — I enumerated every in-app writer of
   `content_items.canonical_url` (7 grep hits: two registry-derived runtime writers, one
   registry-derived ORM hook, one douyin-only legacy migration backfill, two dead adapter DTOs)
   and none can produce a foreign host. I could not inspect **out-of-tree** data sources
   (pre-v0.7 databases, manual edits, restore/import paths), which is where a non-registry URL
   would have to come from.
5. **The full 545-test suite was run once** (plus the 23-test guard file in the scratch copy for
   every mutation); WS3 ran it twice.
6. **Frontend and packaging** — backend only.

## 9. Reproduction assets

Nothing in the repo was written except this report. Scratch (re-runnable):

```
%TEMP%\ws4probe\harness.py                     # temp-SQLite + stubbed upstream pipeline harness
%TEMP%\ws4probe\probe_m2_duration.py           # spy + partition property + before/after literals
%TEMP%\ws4probe\probe_m2_null.py               # NULL storable? three-valued SQL semantics
%TEMP%\ws4probe\probe_m2_ws3repro.py           # the literal WS3 one-row duration=-5 reproduction
%TEMP%\ws4probe\probe_m2_branch.py             # which extraction branch per duration shape
%TEMP%\ws4probe\probe_m1_capability.py         # A–G end-to-end capability-gate matrix
%TEMP%\ws4probe\probe_m1_cap_consistency.py    # CAPABILITY_NOTE_OCR read or not
%TEMP%\ws4probe\probe_l1_empty_platform.py     # blank/empty/unregistered platform, paid-stage counters
%TEMP%\ws4probe\probe_l3_chroma.py             # spellings, exceptions, clear_platform residual
%TEMP%\ws4probe\probe_l3_metadata.py           # canonical collection vs non-canonical metadata
%TEMP%\ws4mut\mutate.py                        # mutations 0–6 with the repo venv
%TEMP%\ws4mut\mutate2.py                       # mutations 7–8 (complement property / IS NULL arm)
%TEMP%\ws4mut\followup.py                      # WS3 repro before/after + intermediate M1 variant
```
Run with `cd backend; $env:PYTHONPATH="D:\My_Projects\Akasha-RAG\backend";
.venv\Scripts\python.exe <probe>`; mutations write only under `%TEMP%\ws4mut\backend`.
