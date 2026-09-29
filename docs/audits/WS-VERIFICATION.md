# WS3 — Independent Adversarial Verification of WS1 + WS2

**Verdict: the work is substantially sound, but three of the eleven claims I was asked to
verify do NOT reproduce as stated. None of them is a data-loss or security regression; two are
unclosed silent-default paths and one is an incomplete convergence. Details below; every line
carries the command or `file:line` that proves it.**

| | |
|---|---|
| Verified revision | `HEAD = f507af52be0d6690354f18286e947de3a8232ebb`, worktree diff fingerprint `43731f69cf17a8ca3b4941bdec0f887bdb96fde6` |
| New-file SHA256 (first 16) | `content_kind.py 09494290D557A1CA`, `platform_registry.py 4EDFB34FD22267BD`, `substantive.py 76C97EEBD81E7697`, `test_platform_registry.py 5B9ED064F2A5739A`, `test_substantive_thresholds.py 19A758CC674653A7`, `test_transcript_source_attribution.py 90A9F72191E3D094` |
| Test suite | `cd backend; .venv\Scripts\python.exe -m pytest -q` → **`522 passed, 3 warnings, 7 subtests passed`** (run twice: 43.9 s and 48.5 s, both exit 0) |
| Lint gate (CI command) | `cd backend; uv run --with ruff ruff check --select E9,F63,F7,F82 app tests` → **`All checks passed!`** (exit 0) |
| Read-only | No source file was modified by me. My only write is this report. |

### Mid-verification change by the Lead (disclosed, re-verified)

At ~18:11–18:12 local the Lead changed `backend/app/services/chroma_service.py:69-71`
(`platform: str` with **no default**), `backend/tests/test_chroma_writes.py:33,56` (added
`platform="douyin"`) and replaced WS1's runtime-`ValueError` test in
`backend/tests/test_platform_registry.py:116-132` with
`test_upsert_requires_platform_so_it_cannot_default_to_douyin` (asserts via `inspect.signature`
that the parameter has no default **and** that omitting it raises `TypeError`).

All results below are against the post-change, frozen revision: newest mtime among changed files
is `test_platform_registry.py 18:12:15`, and all probes/suite runs executed at 18:13–18:21.
WS1's "could not make it strictly required" statement is obsolete: in the current worktree the
claim is **true in the strongest, structural form**.

---

## 1. Baseline — PASS

```
$ cd backend; .venv\Scripts\python.exe -m pytest -q
522 passed, 3 warnings, 7 subtests passed in 43.86s   (exit 0)
```
Pre-work baseline was 436 passed; **+86, no decrease, no failures.**

## 2. WS1 claims

### 2.1 "Unknown platforms can no longer produce a fabricated douyin URL" — **PARTIAL (2 findings)**

**What holds — PASS.** `platform_registry.build_canonical_url` returns `""` for unregistered
ids, and every migration of the old ternary goes through it:

| Site | Now |
|---|---|
| `favorites_service.py:480` | `canonical = build_canonical_url(platform, remote_id)` |
| `chroma_service.py:90`, `:150` | `canonical_url or build_canonical_url(...)` |
| `rag_service.py:739`, `:902`, `:1164`, `:1180` | `build_canonical_url(...)` |
| `entities.py:346-352` (before_insert hook) | refuses to fabricate; raises instead |

Probe (`probe3_migration_registry.py`, all PASS):
`build_canonical_url(p,"x") == ""` for `zhihu|youtube|""|None`; `factory.get_adapter("zhihu")`
and `("youtube")` raise `UnsupportedPlatformError`; `get_platform` raises for
`zhihu|xiaohongshu|youtube|douban|tiktok|""|None`.

**Finding W1-A (MEDIUM, latent).** `backend/app/services/knowledge_service.py:468` still builds a
douyin URL by hand, and it is the **`else` branch** (`:463`) that catches *everything* that is
not a Douyin note and not Bilibili:

```python
463:                else:
467:                        audio_path = download_audio(
468:                            f"https://www.douyin.com/video/{platform_item_id}",
```
Today the branch is only reachable by `douyin` rows, so nothing is broken *now*. The moment a
third platform is registered in `platform_registry.PLATFORMS`, `platform_capability_precheck`
passes it, neither `needs_note_extraction` (`:325`) nor `item_platform == "bilibili"` (`:395`)
matches, and it lands here: an ASR download of a **fabricated Douyin URL**. The registry makes
adding a platform a one-line change, which is exactly what makes this a live landmine. WS1's
forbidden-pattern guard (`test_platform_registry.py:167-173`, test at `:176-198`) cannot see it: the pattern is
`else f"https://www\.douyin\.com/video/` and this call is a multi-line argument, not an `else f"`.

**Finding W1-B (LOW-MEDIUM, reachable with dirty data — falsifies the claim as stated).**
`knowledge_service.py:316` launders a missing platform into douyin *before* the registry is
consulted:

```python
316:                    item_platform = item.platform or "douyin"
...
334:                    capability_error = platform_capability_precheck(item_platform)
```

`ContentItem.platform` is `NOT NULL` (`entities.py:105`) but has **no CHECK constraint**, so `''`
is storable; `or` maps `''` → `"douyin"`, the precheck sees a registered platform, and the row
runs the full paid pipeline. Minimal reproduction (probe 5, variant B — a row with
`platform=""`, `duration=20`, status `pending`):

```
  status / error_code   : 'done' / None
  download_audio calls  : 1 ['https://www.douyin.com/video/r2']
  ASR calls             : 1
  embedding calls       : 1
  chroma.upsert calls   : 1
```
i.e. paid download + ASR + embedding + vector write, and a **fabricated douyin URL**, for a
platform the registry itself declares unsupported (`platform_capability_precheck("")` returns a
rejection reason). No current writer produces `platform=''` (adapters and the registry write
canonical lowercase ids), so this needs pre-existing dirty data — but it is the exact
"silent default" class the registry was built to remove, and one line (`item.platform` used
verbatim, precheck before any fallback) closes it.

### 2.2 "`upsert_video_chunks` can no longer be called without an explicit platform" — **PASS (structural)**

```
[PASS] platform has no default -> <class 'inspect._empty'>
[PASS] omitting platform raises TypeError at the call site
       -> ChromaService.upsert_video_chunks() missing 1 required positional argument: 'platform'
[PASS] unknown platform rejected with UnsupportedPlatformError
[PASS] exactly 2 real call sites in knowledge_service -> 2
[PASS] call site 1 passes platform=item_platform
[PASS] call site 2 passes platform=item_platform
```
(`probe3_migration_registry.py` sections D/E; signature at `chroma_service.py:69-71`, call sites
at `knowledge_service.py:522-526` and `:546-554`.) The two edited legacy doubles in
`test_chroma_writes.py:33,56` now pass `platform="douyin"`.

### 2.3 "The image security boundary is unchanged; douyin/bilibili domain sets byte-identical" — **PASS**

Strongest available test: I loaded **HEAD's own** `app/core/external_urls.py`
(`git show HEAD:backend/app/core/external_urls.py`) as a standalone module and compared outputs
pairwise over a 492-case hostile matrix (12 platform spellings × 40 URLs, plus non-str
platform/URL inputs):

```
HEAD    : {"bilibili": ["biliimg.com","hdslb.com"], "douyin": ["bytedance.com","bytedcdn.com","byteimg.com","douyinpic.com","ibytedtos.com","pstatp.com","zjcdn.com"]}
REGISTRY: {"bilibili": ["biliimg.com","hdslb.com"], "douyin": ["bytedance.com","bytedcdn.com","byteimg.com","douyinpic.com","ibytedtos.com","pstatp.com","zjcdn.com"]}
core registered_image_platforms(): ('bilibili', 'douyin')
matrix cases compared: 492
MISMATCHES vs HEAD: 0
```
Required edge cases, all verified equal to HEAD and individually asserted:

```
[PASS] suffix trick evilhdslb.com          -> ''
[PASS] IP literal 127.0.0.1                -> ''
[PASS] IPv6 literal [::1]                  -> ''
[PASS] credentials u:p@i0.hdslb.com        -> ''
[PASS] non-443 port :8443                  -> ''
[PASS] plain http                          -> ''
[PASS] fragment stripped                   -> 'https://i0.hdslb.com/f.jpg'
[PASS] non-whitelisted platform 'zhihu'    -> ''
[PASS] empty platform                      -> ''
```
One cosmetic note: `https://i0.hdslb.com:443/x` is returned as `https://i0.hdslb.com/x`
(`urlunsplit` drops the default port) — HEAD does the identical thing (0 mismatches), so this is
not a change. Only the *mechanism* moved; the mechanism body (`external_urls.py:47-84`) is
line-for-line HEAD's.

### 2.4 "note/video classification now has exactly ONE rule" — **FAIL (finding W1-C, MEDIUM-LOW)**

Ten literal duration expressions survive in `knowledge_service.py`, none of which delegate to
`content_kind`:

| file:line | expression | classification |
|---|---|---|
| `knowledge_service.py:149` | `ContentItem.duration > 0` (start_sync video filter) | independent rule |
| `knowledge_service.py:152` | `(duration == 0) \| (duration.is_(None))` (start_sync note filter) | independent rule |
| `knowledge_service.py:713` | `duration > 0` (total_video) | independent rule |
| `knowledge_service.py:719` | `== 0 \| is None` (total_note) | independent rule |
| `knowledge_service.py:727`, `:732`, `:737` | `duration > 0` (v_pending/v_done/v_failed) | independent rule |
| `knowledge_service.py:746`, `:755`, `:764` | `== 0 \| is None` (n_pending/n_done/n_failed) | independent rule |
| `knowledge_service.py:900-901`, `:912`, `:915`, `:941` | `video_predicate` / `note_predicate` / `content_kind_for` | delegates ✔ |
| `favorites_service.py:121,131,460-470,967,1028` | shared helpers | delegates ✔ |
| `adapters/douyin.py:92`, `:102` | `dur == 0 or dur is None`, `item_type` ternary | independent rule (dead path) |
| `migration.py:352` | `CASE WHEN COALESCE(duration,0) <= 0` | independent rule (one-off legacy backfill, documented) |

The two rules **disagree for negative durations** (`<= 0` vs `== 0 | IS NULL`), which is precisely
the contradiction `content_kind.py:5-15` claims to have eliminated. Minimal reproduction
(probe 5, variant C — one `content_item` with `duration = -5`, `status = pending`):

```
  list_pending_items(content_type='note')  -> total=1 note_count=1 items=[('neg-1', -5, 'note')]
  start_sync(content_type='note')          -> {'task_id': None, 'pending_count': 0,
                                               'message': '没有待入库的图文笔记，请先同步收藏夹'}
  raw literal note expression  (stats path) -> 0
  shared note_predicate        (list path)  -> 1
  => two rules disagree on a negative-duration row: True
```
User-visible: the row is displayed as 图文 with `note_count=1` on the list page, but the sync
button refuses it as 图文 and the dashboard's note counter says 0. Reachability needs a negative
duration (provider payload or legacy row); for `NULL`/`0`/positive the two rules agree, so this
is a residual inconsistency rather than a routine failure. `content_kind.py`'s own docstring
(`:7-15`) advertises the unification as complete.

## 3. WS2 claims

### 3.1 "No ContentPart can be created claiming a transcript source that was never produced" — **PASS for creation sites, 2 caveats**

Creation sites in `app/`: `favorites_service.py:548-551` and `:615-618` — neither passes
`transcript_source`; column default is `TRANSCRIPT_SOURCE_UNSET` (`entities.py:380`, `:415-417`).
Verified end-to-end by the new test and independently by probe 2:

```
ORM default arg                    : ''
(a) legacy-DB ORM insert stored    : ['']            -> sentinel works: True
(b) raw insert omitting column     : ['', 'whisper_asr'] -> legacy default leaked: True
(c) counterfactual (column absent) : ['', 'whisper_asr', 'whisper_asr']
fresh DB has server DEFAULT clause : False (stored [''])
SQL emitted: INSERT INTO content_parts (..., transcript_source, ...) VALUES (..., '', ...)
```
**Investigation 1 — the sentinel DOES prevent the failure mode it claims to.** The premise is
real and I reproduced both halves:

* an existing (already-migrated) DB still carries the old server default —
  `probe3` section B: `migrate()` on a DB whose ledger already holds
  `v0_7_0_multiplatform` returns `skipped` and the DDL stays
  `transcript_source VARCHAR(32) NOT NULL DEFAULT 'whisper_asr'`; the version constant was not
  bumped (`migration.py:39`, gate at `:187-189`), and the new `DEFAULT ''` only affects a
  **first-time** v0.6→v0.7 migration (probe 3 section A confirms that path now yields
  `DEFAULT ''` and backfills `('',)`);
* SQLAlchemy's client-side `default=` *is* included in the INSERT column list (SQL echoed above),
  so SQLite never falls back to the stale server default for ORM inserts.

**Caveat 1 (LOW, latent).** The protection is ORM-only. Any raw/Core insert that omits the column
on an upgraded DB silently resurrects `'whisper_asr'` — proven in row (b) above. There is no such
site today (`grep ContentPart(` → only the two ORM `db.add` calls), so this is a trap for future
code, and the `entities.py:403-417` comment is slightly stronger than reality ("在所有库上都压得
住那个陈旧默认值" is true only for ORM inserts).

**Caveat 2 (LOW, limitation, not a regression).** Rows that already carry `'whisper_asr'` in an
upgraded DB are **not** corrected — no backfill exists, and the one-off migration is skipped, so
legacy Bilibili parts keep the false ASR label forever. WS2 did not claim a backfill; recorded so
the "no ContentPart can claim…" statement is not read as historical.

Asymmetry worth noting: the Douyin/ASR path never stamps a source at all, so those parts stay
`UNSET` even after a transcript is produced (honest, but the column remains unusable for
provenance on that path).

**Investigation 2 — "fetch_transcript() returns only `str`" is CONFIRMED.**
`bilibili/content_fetcher.py:71-78` declares `-> str`; the subtitle path returns
`f"【视频字幕】{full_title}\n\n{sub_text.strip()}"` (`:143`) and the ASR path returns
`f"【视频语音转写】{full_title}\n\n{asr_text.strip()}"` (`:196`). There is no enum, tuple, or
dataclass to carry the discriminator, so `knowledge_service.py:446-461` cannot know which branch
ran. Writing `TRANSCRIPT_SOURCE_UNKNOWN` (`entities.py:384`) instead of guessing is the correct
call, and the new test asserts the part is not mislabelled `whisper_asr` while still proving the
transcript was indexed (`test_transcript_source_attribution.py:205-219`). Only residual quibble:
the two prefixes *are* self-produced constants, so a typed return value would have been available
without text matching — but that requires editing a file WS2 did not have in scope, which it
disclosed.

### 3.2 "Thresholds 10/20/50 unchanged and NO gate got looser" — **PASS on values; convergence incomplete (W2-A, LOW-MEDIUM)**

```
[PASS] values are exactly 10/20/50 -> MIN_INDEXABLE_CHARS=10 TITLE_FALLBACK_MIN_CHARS=20
                                      BILIBILI_SUBSTANTIVE_TEXT_MIN_CHARS=50
[PASS] bilibili runtime copy == 50
[PASS] content_fetcher.py is untouched (git diff HEAD -- ...content_fetcher.py == "")
```
No loosening, proven input-by-input against the historical expression
`not text or len(text.strip()) < 10` (probe 6A): identical for `None`, `''`, whitespace-only,
9/10/11 chars, and space-padded 10 chars; and the title gate `>= 20` is identical for
`''`/19/20/21 (`None` previously crashed with `TypeError`, now returns `False` — a rejection
becomes a rejection). The migration backfill `>= 50` is byte-unchanged (`migration.py:377`).

**Finding W2-A.** `substantive.py` is not yet the single source it claims to be:
`adapters/douyin.py:124` (`len(extracted_text.strip()) >= 10`), `:126` (`len(clean_title) >= 20`)
and `:140` (`len(transcript_text.strip()) < 10`) are still independent literals — the same 10/20
gate, in a second owner. `substantive.py:5-13`'s inventory omits this file, and the drift guard
(`test_substantive_thresholds.py:83-97`) only greps `knowledge_service.py`, so nothing fails if
these drift. Severity is capped because that adapter method has **no call sites** in `app/` or
`tests/` (`grep 'fetch_item_content('` → the three definitions only), i.e. dead code today.

### 3.3 "An unsupported platform can no longer reach a paid stage" — **PASS for real ids; FAIL for `platform=''`**

`probe5_paid_stage.py` variant A (`platform='zhihu'`, `duration=20`, `pending`):

```
  status / error_code   : 'failed' / 'unsupported_platform'
  error_message         : "不支持的平台: 'zhihu'，当前支持: douyin, bilibili"
  download_audio calls  : 0 []
  ASR calls             : 0
  embedding calls       : 0
  chroma.upsert calls   : 0
  => claim 'unsupported platform never reaches a paid stage': PASS
```
The gate sits at `knowledge_service.py:334-347`, before the `is_note`/bilibili/ASR branches and
before any network or paid call, and writes a machine-readable `error_code`
(`ERROR_CODE_UNSUPPORTED_PLATFORM` at `:45`).

Variant B is the counterexample already described in **Finding W1-B**: `platform=''` is laundered
to `douyin` at `:316` and completes the entire paid pipeline. I could not construct any other
bypass: `fetch_item_content` (the second ASR path, `adapters/douyin.py:139`) has no callers;
`bilibili_content_fetcher.fetch_transcript` and `embedding_client.embed_texts` are only reached
from `knowledge_service` after the gate; API platform parameters are `PlatformFilter`
(`platform_registry.py:185`), which rejects unknown values with a 422 before any handler runs.

### 3.4 `has_substantive_content`: truthful writer or retired? — **PARTIAL (W2-B, LOW)**

- **Not retired**; the column and its 5 write sites remain, and there is **no reader anywhere** —
  not in `backend/app`, `backend/tests`, `frontend/src`, or `scripts` (repo-wide grep; the only
  hits are the schema, the write sites, docs, and the new tests). So the flag is now truthful but
  still unconsumed: the change is invisible to users until something reads it.
- `KnowledgeService.save_state` (`knowledge_service.py:248-259`) computes **both** states from the
  single rule `substantive.has_indexable_text`, and covers the resume path by recomputing from the
  stored transcript. Verified by the suite (flag `True` on success, `True` with checkpoint after an
  embedding failure, `False` on a rejected short transcript, correction of a stale `False`).
- **But it is not the only writer.** Four sites still write `False` directly:
  `favorites_service.py:602`, `api/routes/knowledge.py:392`, `:428`,
  `knowledge_service.py:1001`. I checked each: all four clear `transcript_text` in the same
  statement, so they agree with `has_indexable_text("") is False` — the *value definition* is
  single, the *writer* is not. "Single writer writing both states" is therefore not reproducible
  literally; report it as one value rule + five writers.
- The new tests do not cover any of those four reset paths — they only exercise `save_state`.

## 4. Additional investigations requested

### 4.1 `transcript_source` sentinel vs baked-in `DEFAULT 'whisper_asr'` — reasoning VERIFIED (see 3.1)
Sentinel prevents it for ORM inserts; the claimed failure mode is real for non-ORM inserts.

### 4.2 `fetch_transcript()` typed source genuinely unavailable — VERIFIED (see 3.1)

### 4.3 Image-domain table empty without `app.services` — reasoning VERIFIED, real risk NONE (W1-D, LOW)

```
A) import app.core.external_urls only
   domains: ()            bilibili avatar -> ''      douyin img -> ''
B) import app.main                -> domains: ('bilibili', 'douyin')
C) import app.api.routes.auth     -> domains: ('bilibili', 'douyin')   (app.services loaded: True)
D) pytest -q tests/test_external_image_security.py  -> 5 passed
```
WS1's self-flagged fragility is real: `registered_image_platforms()` is `()` until some
`app.services.*` import runs (`services/__init__.py:23` does the registration). I then hunted for
a realistic entry point that reaches `safe_platform_image_url` **without** it and found **none**:

* every consumer of `safe_platform_image_url` in `app/` lives either in `app/services/*`
  (`account_state.py:13`, `vision_service.py:26`) or in `app/api/routes/auth.py:11`, and `auth.py`
  imports `app.services.account_state` (`:12`), which pulls in the services package;
* `test_external_image_security.py:5-7` imports `app.api.routes.auth` and
  `app.services.vision_service`, which is why the file passes in isolation (point D) — it is not
  order-dependent luck;
* no script under `scripts/` imports `external_urls` at all (repo-wide grep).

So today the empty table is unreachable in-tree, and because the failure mode is fail-closed
(every URL becomes `''`) it is a silent loss-of-avatars bug, never a widened trust boundary. The
residual risk is a future standalone module importing `core.external_urls` directly; the drift
test `test_core_image_domains_come_from_the_registry_without_drift`
(`test_platform_registry.py:135`) cannot catch it because it imports `app.services` itself.
Severity LOW, correctly disclosed.

### 4.4 `delete_video` → `UnsupportedPlatformError` for a legacy row — **NOT a regression (LOW, pre-existing)**

Loaded HEAD's `chroma_service.py` side by side with the current one (probe 4):

```
_target_platforms('zhihu'):  HEAD -> ValueError('不支持的平台: zhihu')            isValueError=True
                            CURRENT -> UnsupportedPlatformError("不支持的平台: 'zhihu'…") isValueError=True
_collection_for('zhihu'):    HEAD -> ValueError     CURRENT -> UnsupportedPlatformError (ValueError subclass)
behaviour for `except ValueError` callers is UNCHANGED: True
```
HEAD's `_collection_for` already raised for any platform outside `("douyin","bilibili")`, so
`delete_video` (`knowledge_service.py:989`) and the reset loop (`api/routes/knowledge.py:379`)
behaved identically before this change. Nothing catches `ValueError` around either call and
`main.py` registers no exception handler, so an unregistered platform has always surfaced as
HTTP 500 — the same today, with a narrower (still-`ValueError`) class. Reachability requires a
`content_items` row whose `platform` is not douyin/bilibili: schema-permitted (no CHECK) but no
current writer produces one (adapters and the registry write canonical ids; the legacy backfill
hardcodes `'douyin'` at `migration.py:352`). **Framing correction: WS2 listed this as an adjacent
unfixed issue; it is a pre-existing 500 path, not something the registry introduced.**

Two related leftovers of the same class, both pre-existing:

* **Finding W1-E (LOW).** `chroma_service.py:54-61` validates with the case/whitespace-normalising
  `get_platform(platform)` but then indexes `self._collections[platform]` with the **raw** string.
  A spelling the registry now blesses therefore raises a bare `KeyError`, not a `ValueError`:
  ```
  _collection_for('DoUYin')  -> KeyError('DoUYin')     _collection_for('DOUYIN') -> KeyError('DOUYIN')
  _collection_for(' douyin ') -> KeyError(' douyin ')  _target_platforms('DoUYin') -> KeyError
  ```
  HEAD raised `ValueError` for every non-canonical spelling, so this is a genuine (if only
  dirty-data-reachable) behaviour change that breaks the "new class is a `ValueError` subclass so
  old callers are unaffected" contract. One-line fix direction: index by
  `get_platform(platform).platform`.
* `knowledge_service.py:933` `getattr(r, "platform", "douyin")` is a residual douyin default, but
  the subquery selects `ContentItem.platform` (`:850`), so the fallback is unreachable dead code.
* `chroma_service.py:86` `if platform == "douyin" and part_id == 0` keeps a platform literal in
  the chunk-id scheme (cosmetic; the non-douyin format is the general one).

### 4.5 Removed `SUPPORTED_PLATFORMS` / `COLLECTION_PREFIX` — **PASS, nothing references them**

Repo-wide grep across `backend/` (excluding `.venv`), `frontend/src`, `scripts` and `docs`:

* `chroma_service.SUPPORTED_PLATFORMS` / `chroma_service.COLLECTION_PREFIX` — **zero** references
  outside `docs/PLATFORM_ROADMAP_v3.md:175,181` and `v4.md:230` (which quote the old code as
  history). No `getattr(..., "SUPPORTED_PLATFORMS")`, no string-keyed lookup, no frontend usage.
* The only live `COLLECTION_PREFIX` is the new `platform_registry.py:37`, a fresh constant in a
  different module (not a leftover alias).
* `rag_evaluation.SUPPORTED_PLATFORMS` still exists but is now derived:
  `rag_evaluation.py:32` = `frozenset(("all", *supported_platforms()))`, asserted equal to the
  registry by `test_platform_registry.py:162`.
* Hardcoded `akasha_*` strings survive only as test expectations
  (`test_chroma_multiplatform.py:133`, `test_platform_registry.py:103-104`) and in the Chroma
  on-disk store — consistent with `platform_registry.py:69`.

## 5. Claim-by-claim scoreboard

| # | Claim | Verdict |
|---|---|---|
| WS1-1 | Unknown platforms can no longer produce a fabricated douyin URL | **PARTIAL** — true for all `build_canonical_url` call sites; false for `platform=''` → `knowledge_service.py:316`+`:468` (W1-B); latent for any newly registered platform (`:468`, W1-A) |
| WS1-2 | `upsert_video_chunks` cannot be called without an explicit platform | **PASS** (structural, post-Lead change; both call sites verified) |
| WS1-3 | Image boundary unchanged; domain sets byte-identical | **PASS** (492/492 cases identical to HEAD, identical domain sets, all required edge cases) |
| WS1-4 | Note/video classification has exactly ONE rule | **FAIL** — 10 independent literals in `knowledge_service.py`, 3 in `adapters/douyin.py`; two rules disagree on negative durations (W1-C) |
| WS1-5 | Image table empty without `app.services` (self-flagged) | **Verified, no in-tree entry point** → LOW, fail-closed (W1-D) |
| WS1-6 | `SUPPORTED_PLATFORMS`/`COLLECTION_PREFIX` fully removed | **PASS** |
| WS2-1 | No ContentPart can claim a transcript source never produced | **PASS** for creation sites (sentinel verified against a real legacy DB); 2 caveats: ORM-only protection, legacy rows not corrected |
| WS2-2 | Thresholds 10/20/50 unchanged, no gate loosened | **PASS** on values and direction; **convergence incomplete** — `adapters/douyin.py:124,126,140` (W2-A) |
| WS2-3 | `has_substantive_content` has a single writer writing both states | **PARTIAL** — one value rule, five writers (4 direct `False`), and **no reader exists** (W2-B) |
| WS2-4 | Unsupported platform cannot reach a paid stage | **PASS** for unregistered ids (`zhihu` etc.); **FAIL** for `platform=''` (W1-B) |
| WS2-5 | `transcript_source` needs `""` not NULL because of the baked-in default | **PASS** — premise reproduced (skipped migration keeps `DEFAULT 'whisper_asr'`; the sentinel is what defeats it) |
| WS2-6 | `fetch_transcript()` returns only `str` (typed source unavailable) | **PASS** — confirmed at `content_fetcher.py:71-78,143,196` |
| WS2-7 | `delete_video` raises for legacy unregistered platform (adjacent issue) | **Reproduced but NOT a regression** — HEAD raised `ValueError` on the identical path |
| WS2-8 | `transcript_source=""` sentinel works on new DBs and upgraded DBs | **PASS** for ORM writes on both; raw-SQL inserts on upgraded DBs still leak `whisper_asr` (LOW) |

## 6. Findings, by severity

**MEDIUM**
1. **W1-A** `knowledge_service.py:463-470` — the catch-all `else` builds
   `https://www.douyin.com/video/{id}` by hand. Registering a third platform makes this the
   landing branch; the registry's forbidden-pattern guard cannot see it.
2. **W1-C** `knowledge_service.py:149,152,713,719,727,732,737,746,755,764` — ten independent
   duration expressions that disagree with `content_kind.note_predicate` for negative durations;
   reproduced end-to-end (list says 图文/1, sync says "没有待入库的图文笔记", stats say 0).

**LOW-MEDIUM**
3. **W1-B** `knowledge_service.py:316` — `item.platform or "douyin"` launders `''` past the
   capability precheck: full paid pipeline + fabricated douyin URL, status `done` (reproduced).
4. **W2-A** `adapters/douyin.py:124,126,140` — a second owner of the 10/20 thresholds, outside
   `substantive.py`'s inventory and outside the drift guard (dead code today).

**LOW**
5. **W1-E** `chroma_service.py:54-61` — mixed-case/whitespace-padded platform ids pass
   `get_platform` but then raise a bare `KeyError` from `self._collections[platform]` (HEAD raised
   `ValueError`).
6. **W2-B** `has_substantive_content` has five writers and **no readers**; the four `False`
   writers are untested.
7. **W1-D** image-domain registration depends on an `app.services` import; unreachable in-tree
   today, fail-closed. `test_external_image_security.py` passes in isolation only because it
   imports `app.api.routes.auth` / `app.services.vision_service`.
8. Raw/Core inserts omitting `transcript_source` on an upgraded DB still yield `'whisper_asr'`
   (`entities.py:403-417` overstates the guarantee); legacy `whisper_asr` rows are never corrected.

**No blocking finding.** No test decrease, no loosened gate, no weakened security boundary, no
data-loss path.

## 7. Claims I could NOT reproduce

1. **WS1: "the note/video classification now has exactly ONE rule."** Ten literal expressions
   remain in `knowledge_service.py` and three in `adapters/douyin.py`; two of them disagree on
   negative durations (reproduced). The most that is true is "one *helper* exists and the list
   path uses it".
2. **WS2: "`has_substantive_content` now has a single writer".** `save_state` is the only writer
   that can produce `True`, but four other sites write `False` directly — and nothing anywhere
   reads the column. "Single value definition, five writers, zero readers" is the accurate
   statement.
3. **WS2: "an unsupported platform can no longer reach a paid stage (download/ASR/embedding)."**
   True for non-empty unregistered ids; false for `platform=''`, which completes download → ASR →
   embedding → vector upsert (reproduced with a one-row fixture).
4. **WS1: "could not make `platform` strictly required because of the legacy doubles."** In the
   frozen revision the Lead's change *did* make it strictly required and the doubles were updated;
   verified structurally via `inspect.signature` + `TypeError` + a full-suite pass. This was a
   reporting artefact of the mid-verification edit, not a surviving limitation.
5. **WS2: "`delete_video` now raises `UnsupportedPlatformError` for a legacy row"** reproduces,
   but not as a *new* behaviour: HEAD raised `ValueError` from the identical code path, so no
   regression can be attributed to the registry change.

## 8. Reproduction assets

No source file was touched. Probe scripts (outside the repo, re-runnable):

```
%TEMP%\ws3probe\probe1_image_boundary.py      # 492-case HEAD-vs-current boundary equivalence
%TEMP%\ws3probe\probe2_transcript_sentinel.py # legacy server default vs ORM sentinel, with SQL echo
%TEMP%\ws3probe\probe3_migration_registry.py  # real migrate() run, skip path, registry behaviour, call sites
%TEMP%\ws3probe\probe4_delete_video.py        # HEAD vs current exception classes
%TEMP%\ws3probe\probe5_paid_stage.py          # paid-stage adversarial matrix + negative-duration divergence
%TEMP%\ws3probe\probe6_gates_case.py          # gate equivalence table + case-normalisation mismatch
```
Run with `cd backend; $env:PYTHONPATH="D:\My_Projects\Akasha-RAG\backend";
.venv\Scripts\python.exe <probe>`.
