# R0: Measurable RAG Evaluation Baseline

**Status:** Design approved; retrieval-only core implementation in progress

**Last updated:** 2026-09-26

**Audience:** engineers changing retrieval, indexing, prompting, reranking, or answer generation; reviewers of those changes; maintainers who run local evaluations.

**Owner:** Akasha-RAG maintainers

**Scope:** local, repeatable evaluation of the existing RAG behavior.

**Non-scope:** changing online retrieval, ranking, chunking, prompts, model selection, or index lifecycle.

**Implemented in this phase:** schema validation, synthetic fixture, and deterministic retrieval/context metric calculation. Real dataset, frozen-index, trace, and model integration remain later phases.

## 1. Decision summary

R0 introduces a private, hand-verified evaluation set and a runner that compares one fixed evaluation set against one fixed index/pipeline version. The repository contains the schema, metric definitions, trace contract, runner contract, and an optional synthetic fixture only. Real questions, answers, source labels, and knowledge-base content stay outside Git.

The primary report has four independent result groups:

1. **Retrieval:** Recall@k, MRR, and nDCG for the ranked candidate list.
2. **Context:** Recall@k for the chunks actually passed to the model after filtering, truncation, and the context budget.
3. **Answer grounding:** manually labelled citation correctness and no-basis answer rate.
4. **Operational cost:** p50/p95 end-to-end latency, context-token estimate, model-call count, and failure/cancellation count.

The deterministic retrieval and operational metrics are the release gate. Citation correctness may be supplemented by an LLM judge, but a judge score never replaces the human gold labels or silently changes the pass/fail result.

## 2. Current-system mapping

The current implementation already exposes or persists the following useful identifiers:

| R0 field | Current source | Use |
|---|---|---|
| `route_type` | `RagService._route()` and `ChatMessage.route_type` | Separate direct, database-list, database-content, and vector behavior. |
| `platform` / collection scope | `/chat/ask` request and `RagService._resolve_collection_scope()` | Reproduce the exact retrieval scope, including `(platform, remote_item_id)` identity. |
| `chunk_id` | Chroma metadata and `ChatMessage.retrieved_chunk_ids` | Stable retrieval and context labels. |
| `platform_item_id` | Chroma metadata and persisted source records | Item-level relevance labels and source grouping. |
| `score` | Chroma result converted from cosine distance | Ranked retrieval metrics and diagnostics. |
| `source_fingerprint` | `ContentItem` | Identify the source revision represented by an indexed item. |
| `pipeline_version` | `IngestionItem` | Identify the ingestion/chunking pipeline revision. |
| `index_manifest` | `IngestionItem` | Identify the index build inputs and state. |
| `model` | `ChatMessage.model` | Identify the generation model used for a run. |
| latency steps | `RagService` trace construction | Preserve route, retrieval, context, and generation timing in the R0 trace. |

The current runtime trace includes a short text excerpt in `trace.chunks`. R0 must not persist or emit that field in an evaluation artifact. The R0 trace contract below deliberately uses identifiers and metadata only. Implementing that sanitized trace is a later implementation step; this document does not change the online response.

## 3. Evaluation-set format

### 3.1 Storage and privacy

The real evaluation set is a local JSONL file outside the repository, for example:

```text
%AKASHA_RAG_EVAL_DIR%\r0\eval-v1.jsonl
```

The directory must not be inside the repository, a shared scratchpad, a backup synced to third parties, or a CI artifact store unless the knowledge-base owner explicitly approves that destination. The runner accepts the path as an argument and never copies the source text into its report.

The repository may contain a synthetic fixture with fake titles, IDs, and answers to test parsing and metric arithmetic. It must not contain user questions, transcripts, summaries, URLs, or manually labelled real content.

### 3.2 Case schema

Each JSONL line is one case. The schema is versioned independently from the runner:

```json
{
  "schema_version": 1,
  "case_id": "r0-v1-0001",
  "language": "zh-CN",
  "category": "exact_fact",
  "question": "示例问题，真实评测文件只保存在本地",
  "scope": {
    "collection_id": "collection-remote-id-or-null",
    "platform": "douyin",
    "expected_scope_items": [
      {"platform": "douyin", "platform_item_id": "item-1"}
    ]
  },
  "answerability": "answerable",
  "gold": {
    "relevant_items": [
      {"platform": "douyin", "platform_item_id": "item-1", "relevance": 3}
    ],
    "relevant_chunks": [
      {"chunk_id": "douyin:item-1:0:3", "relevance": 3}
    ],
    "required_claims": [
      {"claim_id": "c1", "text": "仅供人工评测文件使用", "supporting_chunks": ["douyin:item-1:0:3"]}
    ],
    "expected_answer": "仅供人工评测文件使用",
    "no_basis_reason": null
  },
  "notes": "评测者记录的边界条件，不发送给模型"
}
```

Rules:

- `case_id` is immutable. A changed question or gold label creates a new evaluation-set version rather than editing history in place.
- `category` is one of: `exact_fact`, `numbers`, `colloquial`, `cross_item_synthesis`, `scoped_collection`, `scoped_platform`, `multipart_video`, `unanswerable`, `stale_or_removed`.
- `answerability` is `answerable`, `unanswerable`, or `invalidated`. Invalidated cases are excluded from metrics and reported separately.
- `scope.platform` is `all`, `douyin`, or `bilibili`; `collection_id` is null for an unscoped case.
- Each answerable case must have at least one item-level gold label. Chunk-level labels are required for Recall@k and nDCG at chunk granularity; an item-only case is allowed only when the index cannot expose stable chunk IDs and is reported as item-level evaluation.
- Relevance grades are `0` (not relevant), `1` (useful context), `2` (supports part of the answer), and `3` (directly supports a required claim).
- An unanswerable case has an empty `relevant_items` list and a non-empty `no_basis_reason`.
- `required_claims` are short, atomic propositions. A claim may have multiple supporting chunks.
- `expected_answer` is a human reference for review, not a string-equality target. Paraphrases are accepted when the required claims and citations are correct.

### 3.3 Dataset construction and review

The first set should contain at least 60 cases, with a minimum of five per category and at least ten unanswerable cases. The target distribution is:

| Category | Minimum | Purpose |
|---|---:|---|
| Exact nouns and numbers | 10 | Detect missing or incorrect factual retrieval. |
| Colloquial phrasing | 5 | Detect sensitivity to natural user wording. |
| Cross-item synthesis | 8 | Require evidence from multiple source items. |
| Collection/platform scope | 10 | Detect cross-platform and cross-collection leakage. |
| Multipart Bilibili content | 5 | Detect part and chunk identity errors. |
| Unanswerable | 10 | Measure unsupported answers. |
| Stale/removed content | 5 | Detect retrieval of invalid or superseded content. |
| Other regression cases | 7 | Preserve bugs found during later work. |

Every case is reviewed by two people. Reviewers independently label relevant items, relevant chunks, answerability, required claims, and supporting chunks. Disagreements are resolved in a short decision log stored beside the private dataset. The dataset version records the final reviewer IDs as pseudonymous labels, not personal data.

## 4. Runner contract

The first implementation should expose a command equivalent to:

```text
python -m scripts.rag_eval \
  --dataset <private>/r0/eval-v1.jsonl \
  --index-manifest <private>/r0/index-manifest.json \
  --output <private>/r0/reports/<run-id>.json \
  --trace-output <private>/r0/traces/<run-id>.jsonl \
  --mode replay
```

Required runner behavior:

1. Validate every case before execution and fail before making model calls if the schema, dataset version, scope, or gold labels are malformed.
2. Pin the evaluation-set SHA-256, runner version, Python version, Git SHA, index manifest SHA-256, pipeline version, model identifier, retrieval settings, and prompt version in the report.
3. Run cases in deterministic `case_id` order. A fixed random seed is required for any sampling; default sampling is disabled.
4. Execute the same scope semantics as `/chat/ask`: `all` is represented as no platform restriction, and a collection is resolved as a `(platform, remote_item_id)` set.
5. Record one result and one trace per case, including failures. A failed case is not silently dropped.
6. Support `--retrieval-only` for metrics that do not require an LLM call and `--full` for answer and citation evaluation.
7. Never write question text, expected answers, source text, prompt text, or API keys to the report or trace. They remain in the private dataset or process memory.
8. Produce a machine-readable JSON report and a concise Markdown summary. The report is the comparison artifact used by later PRs.

The runner must use a frozen index. If the index manifest or any referenced source fingerprint changes during a run, the run fails rather than mixing versions.

## 5. Trace contract

The trace is JSONL, one object per case. It is diagnostic and privacy-minimized:

```json
{
  "trace_schema_version": 1,
  "run_id": "2026-09-26T120000Z-r0-v1",
  "case_id": "r0-v1-0001",
  "index": {
    "manifest_sha256": "...",
    "pipeline_version": "...",
    "source_fingerprints": ["..."],
    "chroma_collection": "akasha_douyin"
  },
  "request": {
    "scope_platform": "douyin",
    "collection_id_hash": "sha256:...",
    "route_type": "vector"
  },
  "retrieval": {
    "requested_top_k": 8,
    "fetch_k": 32,
    "mmr_lambda": 0.55,
    "candidates": [
      {
        "rank": 1,
        "chunk_id": "douyin:item-1:0:3",
        "platform": "douyin",
        "platform_item_id_hash": "sha256:...",
        "content_item_id": 12,
        "score": 0.8123,
        "filtered": false,
        "filter_reason": null
      }
    ],
    "final_context_chunk_ids": ["douyin:item-1:0:3"],
    "context_truncated": false
  },
  "timing_ms": {
    "route": 1,
    "retrieval": 42,
    "context": 3,
    "generation": 810,
    "total": 856
  },
  "generation": {
    "model": "model-id",
    "model_call_count": 1,
    "context_token_estimate": 742,
    "answer_token_estimate": 96,
    "cancelled": false,
    "failed": false
  },
  "citations": [
    {"claim_id": "c1", "chunk_id": "douyin:item-1:0:3", "valid": true}
  ]
}
```

Filtering reasons are an allowlist: `scope`, `platform`, `inactive_item`, `not_done`, `deduplicated`, `mmr`, `top_k`, `context_budget`, `invalid_metadata`, or `error`. The trace records counts and identifiers, never source text. Collection IDs and remote item IDs are hashed because the evaluation report may be copied for review.

The current `RagService` trace includes text excerpts and therefore is not the R0 trace format. The implementation must construct a separate sanitized evaluation trace or sanitize the runtime trace before persistence.

## 6. Metric definitions

All retrieval metrics are computed per case and macro-averaged across valid cases. Reports include both the aggregate and the per-category breakdown.

### Retrieval metrics

For a ranked list of chunks or items `r_1 ... r_k` and gold relevance `rel_i`:

- **Recall@k:** `|gold_relevant ∩ retrieved_top_k| / |gold_relevant|`. For a multi-source case, any gold item counts once; an unanswerable case is excluded from retrieval recall and reported under no-basis metrics.
- **MRR:** `1 / rank(first gold-relevant result)`, or `0` when no relevant result occurs in the evaluated list.
- **nDCG@k:** `DCG@k / IDCG@k`, where `DCG@k = Σ((2^rel_i - 1) / log2(i + 1))`. The report uses the integer relevance grades from the dataset.
- **Context Recall@k:** the same recall formula over `final_context_chunk_ids`, after scope filtering, deduplication, MMR, and the context-character budget. This distinguishes retrieval failure from context-construction loss.

Default cutoffs are `k = 1, 3, 5, 8`. `k=8` matches the current `retrieval_top_k` default. The report must retain the full ranked list up to `fetch_k` for diagnosis, while metric values are computed at the declared cutoffs.

### Answer and citation metrics

- **Citation correctness:** for every required claim in an answerable case, a citation is correct only when the cited chunk is in the current turn's final context and the human gold label says it supports that claim. Report claim precision, claim recall, and the fraction of answers with all required claims correctly cited.
- **No-basis answer rate:** among cases labelled `unanswerable`, the fraction where the answer makes a substantive factual claim instead of explicitly stating that the available sources do not establish an answer. Human review decides whether a response is substantive; empty/refusal answers are not automatically correct for answerable cases.
- **Answer completeness:** optional diagnostic metric: fraction of required claims present in the answer, independent of citation correctness. It is not a release gate until the claim annotation process is stable.

Citation correctness is labelled in two passes:

1. Two human reviewers label claim support and resolve disagreements. This is the authoritative score.
2. An optional LLM judge receives only the answer, claim, citation IDs, and redacted evidence identifiers/content supplied by the evaluator. Its score is stored separately with judge model/version and prompt hash. Judge disagreement is reported, never hidden.

### Operational metrics

- **Latency:** end-to-end wall-clock latency from runner request start to completed answer; report p50, p95, min, max, and failures. Also report route, retrieval, context, and generation components when available.
- **Context tokens:** tokenizer estimate for the final model context. The report must name the tokenizer and version; character count is retained as a fallback diagnostic only.
- **Answer tokens:** tokenizer estimate for the generated answer.
- **Model calls:** count of embedding, LLM, and optional judge calls. Retrieval-only runs report zero generation calls rather than omitting the field.
- **Failure rate:** failed or cancelled cases / attempted cases, with failure reasons and counts.

## 7. Comparison and regression policy

Every later RAG PR that changes retrieval, indexing, prompts, reranking, chunking, or generation must attach a report comparison against the latest accepted R0 baseline. The comparison must use:

- the same dataset SHA-256;
- the same evaluation-set version and gold labels;
- a declared index manifest and pipeline version;
- the same metric cutoffs and runner version, or an explicit migration note;
- the same model and generation settings for answer metrics, unless the PR is specifically changing them.

Default regression gates are:

| Metric | Required rule |
|---|---|
| Retrieval Recall@5 | Must not decrease by more than 0.03 absolute overall or 0.05 in any category. |
| Retrieval nDCG@5 | Must not decrease by more than 0.03 absolute overall. |
| Context Recall@5 | Must not decrease by more than 0.03 absolute overall. |
| No-basis answer rate | Must not increase by more than 0.05 absolute. |
| Citation correctness | Must not decrease by more than 0.05 absolute after human review. |
| Latency p95 | Must not increase by more than 20% without an explicit performance decision. |
| Failure/cancellation rate | Must not increase by more than 1 percentage point. |

These thresholds are initial release gates, not claims about statistical significance. A PR may override a gate only with a written rationale, category-level analysis, and maintainer approval recorded in the PR. A changed dataset, index, model, or runner version creates a new comparison baseline rather than making an incomparable result look like a regression.

## 8. Required report shape

The Markdown summary must include:

1. run ID, dataset SHA, index manifest SHA, Git SHA, runner version, model, and settings;
2. case counts by category and answerability;
3. retrieval, context, answer/citation, and operational tables with overall and per-category values;
4. comparison deltas against the accepted baseline;
5. the list of failed, invalidated, and threshold-breaching cases by `case_id`;
6. links or paths to the private trace file, never embedded source text;
7. an explicit statement when a metric is unavailable or judged by an optional LLM judge.

The JSON report is authoritative for automation. The Markdown file is a review projection and must be generated from the JSON rather than edited by hand.

## 9. Implementation sequence after design approval

1. Add schema validation and a synthetic fixture only.
2. Add deterministic retrieval-only runner and sanitized trace writer.
3. Add private dataset import/validation and manual label review workflow.
4. Add full-answer execution with a mock LLM mode for local runner tests, then an opt-in real-provider mode.
5. Add metric calculation and JSON/Markdown report generation.
6. Run the first private baseline and record its dataset/index/model versions in a separate review artifact.
7. Only after the baseline is accepted, require later RAG PRs to attach comparisons.

## 10. Acceptance checklist

- [ ] Real evaluation data is local-only and excluded from Git and shared artifacts.
- [ ] Dataset schema validates scope, answerability, item/chunk gold labels, claims, and version.
- [ ] Runner pins dataset, index, pipeline, model, prompt, settings, code, and tool versions.
- [ ] Retrieval and final-context metrics are reported separately.
- [ ] Human citation labels are authoritative; optional LLM judge output is separated.
- [ ] Traces contain IDs, hashes, scores, filter reasons, versions, and timings, but no source text or secrets.
- [ ] Reports are deterministic, machine-readable, and comparable by dataset/index version.
- [ ] Regression thresholds and an override process are documented.
- [ ] The design implementation order is separate from online behavior changes.
