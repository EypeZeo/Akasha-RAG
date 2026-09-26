"""Command-line entry point for the local R0 retrieval evaluator.

Examples, from the repository root:

    python -m scripts.rag_eval validate --dataset <private>/r0/eval-v1.jsonl
    python -m scripts.rag_eval metrics --dataset <private>/r0/eval-v1.jsonl \
        --observations <private>/r0/synthetic-observations.json \
        --output <private>/r0/report.json

The first implementation is retrieval-only. It never opens the database,
Chroma, a provider SDK, or an LLM.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.rag_evaluation import (  # noqa: E402
    answer_report,
    EvaluationError,
    load_dataset,
    load_answer_observations,
    load_index_manifest,
    load_observations,
    replay_evaluation,
    retrieval_report,
    write_sanitized_traces,
)


def _write_json(value: dict, output: str | None) -> None:
    text = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if output:
        Path(output).write_text(text, encoding="utf-8", newline="\n")
    else:
        sys.stdout.write(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate", help="validate a private evaluation JSONL file")
    validate.add_argument("--dataset", required=True)
    metrics = sub.add_parser("metrics", help="calculate deterministic retrieval metrics")
    metrics.add_argument("--dataset", required=True)
    metrics.add_argument("--observations", required=True)
    metrics.add_argument("--output")
    metrics.add_argument("--cutoff", action="append", type=int, dest="cutoffs")
    trace = sub.add_parser("trace", help="write privacy-minimized retrieval traces")
    trace.add_argument("--dataset", required=True)
    trace.add_argument("--observations", required=True)
    trace.add_argument("--output", required=True)
    trace.add_argument("--run-id", required=True)
    trace.add_argument("--index-manifest-sha256", required=True)
    trace.add_argument("--pipeline-version", required=True)
    trace.add_argument("--source-fingerprint", action="append", default=[])
    trace.add_argument("--chroma-collection")
    trace.add_argument("--model", default="")
    replay = sub.add_parser("replay", help="replay fixed observations against a frozen index manifest")
    replay.add_argument("--dataset", required=True)
    replay.add_argument("--observations", required=True)
    replay.add_argument("--index-manifest", required=True)
    replay.add_argument("--output", required=True)
    replay.add_argument("--trace-output", required=True)
    replay.add_argument("--run-id", required=True)
    replay.add_argument("--model", default="")
    replay.add_argument("--cutoff", action="append", type=int, dest="cutoffs")
    answer_metrics = sub.add_parser("answer-metrics", help="calculate human answer/citation metrics")
    answer_metrics.add_argument("--dataset", required=True)
    answer_metrics.add_argument("--observations", required=True)
    answer_metrics.add_argument("--answers", required=True)
    answer_metrics.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        dataset = load_dataset(args.dataset)
        if args.command == "validate":
            categories = {}
            for case in dataset.cases:
                categories[case.category] = categories.get(case.category, 0) + 1
            _write_json({
                "valid": True,
                "schema_version": dataset.schema_version,
                "dataset_sha256": dataset.sha256,
                "case_count": len(dataset.cases),
                "categories": dict(sorted(categories.items())),
            }, None)
        elif args.command == "metrics":
            observations = load_observations(args.observations)
            report = retrieval_report(dataset, observations, tuple(args.cutoffs or (1, 3, 5, 8)))
            _write_json(report, args.output)
        elif args.command == "trace":
            observations = load_observations(args.observations)
            count = write_sanitized_traces(
                dataset,
                observations,
                args.output,
                args.run_id,
                index_manifest_sha256=args.index_manifest_sha256,
                pipeline_version=args.pipeline_version,
                source_fingerprints=args.source_fingerprint,
                chroma_collection=args.chroma_collection,
                model=args.model,
            )
            _write_json({"written_traces": count, "output": str(Path(args.output))}, None)
        elif args.command == "answer-metrics":
            observations = load_observations(args.observations)
            answers = load_answer_observations(args.answers)
            _write_json(answer_report(dataset, observations, answers), args.output)
        else:
            observations = load_observations(args.observations)
            index_manifest = load_index_manifest(args.index_manifest)
            report = replay_evaluation(
                dataset,
                observations,
                index_manifest,
                args.output,
                args.trace_output,
                args.run_id,
                cutoffs=tuple(args.cutoffs or (1, 3, 5, 8)),
                model=args.model,
            )
            _write_json(report["run"], None)
        return 0
    except EvaluationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
