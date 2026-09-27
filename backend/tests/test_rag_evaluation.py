"""Tests for the private R0 dataset contract and deterministic metrics."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.rag_evaluation import (
    answer_report,
    build_local_index_manifest,
    collect_live_retrieval,
    EvaluationError,
    case_retrieval_metrics,
    load_dataset,
    load_answer_observations,
    load_index_manifest,
    load_observations,
    local_index_preflight,
    ndcg_at_k,
    privacy_hash,
    recall_at_k,
    retrieval_report,
    replay_evaluation,
    write_sanitized_traces,
    write_markdown_summary,
)


FIXTURES = Path(__file__).resolve().parents[2] / "scripts" / "fixtures"


def test_synthetic_dataset_is_valid_and_sorted_by_case_id():
    dataset = load_dataset(FIXTURES / "rag_eval_synthetic.jsonl")

    assert dataset.case_ids == {"synthetic-001", "synthetic-002", "synthetic-003", "synthetic-004"}
    assert [case.case_id for case in dataset.cases] == sorted(dataset.case_ids)
    assert dataset.cases[-1].answerability == "invalidated"


def test_synthetic_retrieval_report_excludes_invalidated_cases():
    dataset = load_dataset(FIXTURES / "rag_eval_synthetic.jsonl")
    observations = load_observations(FIXTURES / "rag_eval_synthetic_observations.json")

    report = retrieval_report(dataset, observations)

    assert report["case_counts"] == {
        "total": 4,
        "valid": 3,
        "invalidated": 1,
        "by_category": {
            "cross_item_synthesis": 1,
            "exact_fact": 1,
            "stale_or_removed": 1,
            "unanswerable": 1,
        },
        "by_answerability": {"answerable": 2, "invalidated": 1, "unanswerable": 1},
    }
    assert report["overall"]["chunk"]["recall@1"] == 0.75
    assert report["overall"]["chunk"]["recall@3"] == 1.0
    assert report["overall"]["item"]["mrr"] == 1.0
    assert report["overall"]["context"]["recall@1"] == 0.75
    assert len(report["cases"]) == 3


def test_rank_metrics_use_graded_ndcg_and_deduplicate_ranked_ids():
    assert recall_at_k(["a", "a", "b"], {"a", "b"}, 1) == 0.5
    assert ndcg_at_k(["b", "a"], {"a": 3, "b": 1}, 2) < 1.0
    assert ndcg_at_k(["a", "b"], {"a": 3, "b": 1}, 2) == 1.0


def test_case_metrics_report_item_and_chunk_granularities():
    dataset = load_dataset(FIXTURES / "rag_eval_synthetic.jsonl")
    observations = load_observations(FIXTURES / "rag_eval_synthetic_observations.json")
    case = dataset.cases[1]

    metrics = case_retrieval_metrics(case, observations[case.case_id], (1, 3))

    assert metrics["item"]["recall@1"] == 0.5
    assert metrics["item"]["recall@3"] == 1.0
    assert metrics["chunk"]["mrr"] == 1.0
    assert metrics["context"]["recall@1"] == 0.5


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("category", "not-a-category", "category"),
        ("answerability", "unanswerable", "relevant items"),
        ("scope", {"collection_id": None, "platform": "bad", "expected_scope_items": []}, "platform"),
    ],
)
def test_dataset_validation_rejects_invalid_cases(tmp_path, field, replacement, message):
    source = (FIXTURES / "rag_eval_synthetic.jsonl").read_text(encoding="utf-8").splitlines()[0]
    case = json.loads(source)
    case[field] = replacement
    path = tmp_path / "invalid.jsonl"
    path.write_text(json.dumps(case) + "\n", encoding="utf-8")

    with pytest.raises(EvaluationError, match=message):
        load_dataset(path)


def test_dataset_validation_rejects_unknown_fields(tmp_path):
    case = json.loads((FIXTURES / "rag_eval_synthetic.jsonl").read_text(encoding="utf-8").splitlines()[0])
    case["unexpected"] = True
    path = tmp_path / "invalid.jsonl"
    path.write_text(json.dumps(case) + "\n", encoding="utf-8")

    with pytest.raises(EvaluationError, match="unknown field"):
        load_dataset(path)


def test_report_rejects_missing_or_unknown_observations():
    dataset = load_dataset(FIXTURES / "rag_eval_synthetic.jsonl")
    observations = load_observations(FIXTURES / "rag_eval_synthetic_observations.json")
    observations.pop("synthetic-001")
    with pytest.raises(EvaluationError, match="missing case ID"):
        retrieval_report(dataset, observations)

    observations = load_observations(FIXTURES / "rag_eval_synthetic_observations.json")
    observations["unknown"] = observations["synthetic-003"]
    with pytest.raises(EvaluationError, match="unknown case ID"):
        retrieval_report(dataset, observations)


def test_sanitized_trace_contains_no_question_or_source_text(tmp_path):
    dataset = load_dataset(FIXTURES / "rag_eval_synthetic.jsonl")
    observations = load_observations(FIXTURES / "rag_eval_synthetic_observations.json")
    output = tmp_path / "traces" / "run.jsonl"

    count = write_sanitized_traces(
        dataset,
        observations,
        output,
        "synthetic-run",
        index_manifest_sha256="a" * 64,
        pipeline_version="synthetic-pipeline-1",
        source_fingerprints=("synthetic-source-1",),
        chroma_collection="akasha_douyin",
        route_types={"synthetic-001": "vector"},
    )

    assert count == 3
    lines = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 3
    trace = lines[0]
    assert trace["request"]["collection_id_hash"] is None
    assert trace["request"]["route_type"] == "vector"
    assert trace["retrieval"]["candidates"][0]["platform_item_id_hash"].startswith("sha256:")
    assert trace["retrieval"]["candidates"][0]["platform_item_id_hash"] == privacy_hash("item-1")
    serialized = output.read_text(encoding="utf-8")
    assert "What color is the synthetic marker?" not in serialized
    assert "The marker is blue." not in serialized
    assert '"text"' not in serialized


def test_trace_writer_rejects_unpinned_manifest_digest(tmp_path):
    dataset = load_dataset(FIXTURES / "rag_eval_synthetic.jsonl")
    observations = load_observations(FIXTURES / "rag_eval_synthetic_observations.json")

    with pytest.raises(EvaluationError, match="SHA-256"):
        write_sanitized_traces(
            dataset,
            observations,
            tmp_path / "traces.jsonl",
            "synthetic-run",
            index_manifest_sha256="not-a-digest",
            pipeline_version="synthetic-pipeline-1",
        )


def test_replay_pins_index_manifest_in_report_and_trace(tmp_path):
    dataset = load_dataset(FIXTURES / "rag_eval_synthetic.jsonl")
    observations = load_observations(FIXTURES / "rag_eval_synthetic_observations.json")
    manifest = load_index_manifest(FIXTURES / "rag_eval_synthetic_index_manifest.json")
    report_path = tmp_path / "reports" / "run.json"
    trace_path = tmp_path / "traces" / "run.jsonl"

    report = replay_evaluation(dataset, observations, manifest, report_path, trace_path, "synthetic-run")

    assert report["run"]["mode"] == "replay"
    assert report["run"]["index_id"] == "synthetic-index-1"
    assert report["run"]["index_manifest_sha256"] == manifest.sha256
    assert report_path.exists()
    trace = json.loads(trace_path.read_text(encoding="utf-8").splitlines()[0])
    assert trace["index"]["manifest_sha256"] == manifest.sha256
    assert trace["index"]["chroma_collections"] == ["akasha_douyin", "akasha_bilibili"]


def test_index_manifest_rejects_duplicate_platform(tmp_path):
    manifest = {
        "schema_version": 1,
        "index_id": "synthetic-index-1",
        "pipeline_version": "synthetic-pipeline-1",
        "source_fingerprints": [],
        "chroma_collections": [
            {"name": "one", "platform": "douyin"},
            {"name": "two", "platform": "douyin"},
        ],
    }
    path = tmp_path / "index.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(EvaluationError, match="unique"):
        load_index_manifest(path)


def test_answer_report_calculates_citation_and_no_basis_metrics():
    dataset = load_dataset(FIXTURES / "rag_eval_synthetic.jsonl")
    retrieval = load_observations(FIXTURES / "rag_eval_synthetic_observations.json")
    answers = load_answer_observations(FIXTURES / "rag_eval_synthetic_answers.json")

    report = answer_report(dataset, retrieval, answers)

    assert report["overall"] == {
        "case_count": 3,
        "answerable_case_count": 2,
        "unanswerable_case_count": 1,
        "claim_recall": 1.0,
        "citation_precision": 1.0,
        "citation_recall": 1.0,
        "all_claims_correctly_cited_rate": 1.0,
        "no_basis_answer_rate": 0.0,
    }


def test_answer_observations_reject_answer_text(tmp_path):
    path = tmp_path / "answers.json"
    path.write_text(json.dumps({"synthetic-001": {"claims": [], "answer": "secret"}}), encoding="utf-8")

    with pytest.raises(EvaluationError, match="unknown field"):
        load_answer_observations(path)


def test_live_collection_is_read_only_and_uses_injected_services():
    dataset = load_dataset(FIXTURES / "rag_eval_synthetic.jsonl")

    class DbContext:
        def __enter__(self):
            return object()

        def __exit__(self, *args):
            return False

    class FakeChroma:
        def count(self):
            return 1

    class FakeRag:
        def _route(self, query, has_data):
            return "vector"

        def _resolve_collection_scope(self, db, collection_id, platform):
            return None

        def _retrieve_hits_for_route(self, route, query, db, scope_ids, platform):
            return [{
                "chunk_id": "synthetic:chunk",
                "platform": "douyin",
                "platform_item_id": "synthetic-item",
                "content_item_id": 1,
                "score": 0.8,
            }]

    observations, routes, timings = collect_live_retrieval(
        dataset,
        session_factory_override=lambda: DbContext(),
        rag_service_override=FakeRag(),
        chroma_service_override=FakeChroma(),
    )

    assert set(observations) == {"synthetic-001", "synthetic-002", "synthetic-003"}
    assert routes == {case_id: "vector" for case_id in observations}
    assert all(set(timing) == {"route", "retrieval", "context", "generation", "total"} for timing in timings.values())


def test_local_index_preflight_reports_ready_metadata_without_provider_calls():
    class DbContext:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def __init__(self):
            self.scalar_values = iter([3, 3, 6])

        def scalar(self, _query):
            return next(self.scalar_values)

        def execute(self, query):
            query_text = str(query)
            if "GROUP BY ingestion_items.status" in query_text:
                return type("Result", (), {"all": lambda self: [("done", 3)]})()
            return type("Result", (), {
                "all": lambda self: [
                    (1, "v0.7.1", "fingerprint-a", "manifest-a", "douyin", "item-a", "https://example.test/a", "text-a"),
                    (2, "v0.7.1", "fingerprint-b", "manifest-b", "douyin", "item-b", "https://example.test/b", "text-b"),
                    (3, "v0.7.1", "fingerprint-c", "manifest-c", "douyin", "item-c", "https://example.test/c", "text-c"),
                ]
            })()

    class Collection:
        def count(self):
            return 3

        def get(self, include=None):
            return {"metadatas": [{"content_item_id": 1}, {"content_item_id": 2}, {"content_item_id": 3}]}

    class FakeChroma:
        def count(self):
            return 6

        def _collection_for(self, platform):
            return Collection()

    preflight = local_index_preflight(
        session_factory_override=lambda: DbContext(),
        chroma_service_override=FakeChroma(),
    )

    assert preflight["ready"] is True
    assert preflight["index"]["pipeline_versions"] == ["v0.7.1"]
    assert preflight["index"]["source_fingerprints"] == [
        "fingerprint-a", "fingerprint-b", "fingerprint-c"
    ]

    manifest = build_local_index_manifest(
        "local-r0-2026-09-27",
        session_factory_override=lambda: DbContext(),
        chroma_service_override=FakeChroma(),
    )
    assert manifest["pipeline_version"] == "v0.7.1"
    assert manifest["source_fingerprints"] == preflight["index"]["source_fingerprints"]
    assert manifest["chroma_collections"] == [
        {"name": "akasha_douyin", "platform": "douyin"},
        {"name": "akasha_bilibili", "platform": "bilibili"},
    ]


def test_local_index_manifest_fails_closed_when_index_is_empty():
    class DbContext:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def scalar(self, _query):
            return 0

        def execute(self, _query):
            return type("Result", (), {"all": lambda self: []})()

    class FakeChroma:
        def count(self):
            return 0

        def _collection_for(self, platform):
            raise AssertionError("empty index should not need collection access")

    with pytest.raises(EvaluationError, match="local index preflight failed"):
        build_local_index_manifest(
            "empty-index",
            session_factory_override=lambda: DbContext(),
            chroma_service_override=FakeChroma(),
        )


def test_markdown_summary_contains_aggregate_metadata_but_no_case_text(tmp_path):
    dataset = load_dataset(FIXTURES / "rag_eval_synthetic.jsonl")
    observations = load_observations(FIXTURES / "rag_eval_synthetic_observations.json")
    report = retrieval_report(dataset, observations)
    report["run"] = {
        "run_id": "private-run",
        "mode": "replay",
        "runner_version": "r0-replay-1",
        "model": "deepseek-flash",
        "index_id": "private-index",
        "index_manifest_sha256": "a" * 64,
        "pipeline_version": "v0.7.0",
    }
    output = tmp_path / "summary.md"

    write_markdown_summary(report, output)

    text = output.read_text(encoding="utf-8")
    assert "private-run" in text
    assert "exact_fact" in text
    assert "What color is the synthetic marker?" not in text
    assert "The marker is blue." not in text
