"""Tests for the private R0 dataset contract and deterministic metrics."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.rag_evaluation import (
    EvaluationError,
    case_retrieval_metrics,
    load_dataset,
    load_observations,
    ndcg_at_k,
    recall_at_k,
    retrieval_report,
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
