# Private R0 Baseline Runbook

This runbook describes the first real R0 retrieval baseline. It is an operator
workflow because the evaluation questions, gold labels, provider credentials,
and source content are private inputs. None of those inputs belong in GitHub,
issue comments, CI artifacts, or this repository.

## 1. Prepare A Private Workspace

Use a local directory outside the repository and outside synchronized backup
folders. On Windows, for example:

```powershell
$env:AKASHA_RAG_EVAL_DIR = "D:\Akasha-RAG-private\r0"
New-Item -ItemType Directory -Force -Path $env:AKASHA_RAG_EVAL_DIR
```

Keep these files there:

- `eval-v1.jsonl`: the hand-reviewed private cases;
- `reviewer-a.jsonl` and `reviewer-b.jsonl`: independent labels;
- `decision-log.md`: resolved disagreements, using pseudonymous reviewer IDs;
- `index-manifest.json`: generated only after the local index is ready;
- `reports\<run-id>.json`, `reports\<run-id>.md`, and `traces\<run-id>.jsonl`.

The repository contains only synthetic fixtures. Do not paste real questions,
answers, titles, URLs, source text, or API keys into an issue or pull request.

## 2. Verify The Provider Session

Start the application from the merged `main` checkout. Confirm that the
account status is logged in, then run a platform sync from the UI or with the
local API. A successful Douyin sync must report a safe diagnostic containing:

- `outcome: success`;
- `list_export_found: true` and `video_export_found: true`;
- provider `status_code: 0`;
- no cookies, signed URLs, or page source in the diagnostic.

If the provider changes its private Webpack shape, the endpoint returns the
sanitized module/export diagnosis and the collector keeps the last good local
snapshot. Do not clear the local database to recover from a provider failure.

## 3. Build And Check The Frozen Index

After the selected content has been ingested successfully, run this read-only
check from the repository root:

```powershell
python -m scripts.rag_eval preflight --output "$env:AKASHA_RAG_EVAL_DIR\preflight.json"
```

The check must report `ready: true`. It verifies that the index has vectors,
that vectors belong to active completed content, and that one pipeline version
is used. It also reports the active database counts and Chroma collection
counts without exporting source text.

Generate the manifest only after preflight passes:

```powershell
python -m scripts.rag_eval manifest `
  --index-id "r0-v1-2026-09-27" `
  --output "$env:AKASHA_RAG_EVAL_DIR\index-manifest.json"
```

The command derives a stable `sha256:` fingerprint for legacy completed rows
whose historical fingerprint field is empty. It does not modify SQLite or
Chroma. If preflight reports stale vectors, remove only the stale vector by
its `(platform, remote item ID, local content ID)` and rerun the check.

## 4. Prepare And Review The Dataset

Create at least 60 cases in the schema documented in
`docs/design/rag-evaluation-baseline-r0.md`, with at least 10 unanswerable
cases and the required category distribution. Each answerable case needs
item-level gold labels; chunk-level labels are required when stable chunk IDs
are available.

Two reviewers independently label answerability, relevant items, relevant
chunks, required claims, and supporting chunks. Resolve disagreements in the
private decision log. Run schema validation before using any provider calls:

```powershell
python -m scripts.rag_eval validate `
  --dataset "$env:AKASHA_RAG_EVAL_DIR\eval-v1.jsonl"
```

Record the reported dataset SHA-256 in the private decision log.

## 5. Run The Retrieval Baseline

For a read-only live retrieval run, explicitly allow embedding provider calls.
This flag is required because embeddings may incur cost:

```powershell
$run = "r0-v1-2026-09-27"
New-Item -ItemType Directory -Force -Path `
  "$env:AKASHA_RAG_EVAL_DIR\reports", `
  "$env:AKASHA_RAG_EVAL_DIR\traces"

python -m scripts.rag_eval live `
  --dataset "$env:AKASHA_RAG_EVAL_DIR\eval-v1.jsonl" `
  --index-manifest "$env:AKASHA_RAG_EVAL_DIR\index-manifest.json" `
  --output "$env:AKASHA_RAG_EVAL_DIR\reports\$run.json" `
  --markdown-output "$env:AKASHA_RAG_EVAL_DIR\reports\$run.md" `
  --trace-output "$env:AKASHA_RAG_EVAL_DIR\traces\$run.jsonl" `
  --run-id $run `
  --model deepseek-flash `
  --allow-provider-calls
```

The JSON report is authoritative. The Markdown file is a generated review
projection. The trace contains IDs, hashes, scores, timings, and allowlisted
diagnostics only; inspect it before sharing the aggregate report.

## 6. Collect Answer And Citation Labels

Retrieval-only mode does not produce answer, citation, token, or model-call
metrics. Run the answer workflow separately using the same dataset SHA and
index manifest. Store the redacted answer annotations outside the repository,
then calculate the human-label metrics:

```powershell
python -m scripts.rag_eval answer-metrics `
  --dataset "$env:AKASHA_RAG_EVAL_DIR\eval-v1.jsonl" `
  --observations "$env:AKASHA_RAG_EVAL_DIR\observations.json" `
  --answers "$env:AKASHA_RAG_EVAL_DIR\answer-observations.json" `
  --output "$env:AKASHA_RAG_EVAL_DIR\reports\$run-answers.json"
```

Human citation labels are authoritative. An optional judge can be stored as a
separate diagnostic, but it cannot replace the two-reviewer labels.

## 7. Accept Or Block The Baseline

Attach only the following to Issue #59:

- dataset SHA-256 and private storage location;
- index manifest SHA-256, index ID, and pipeline version;
- runner version, model/settings, and Git SHA;
- aggregate retrieval, context, answer/citation, no-basis, latency, token, and
  model-call metrics;
- reviewer decision and any unavailable metrics.

Do not close the issue until all acceptance criteria are satisfied. If the
private dataset, two-reviewer labels, or usable frozen index is missing, leave
the issue open with the exact missing prerequisite and the preflight output.
