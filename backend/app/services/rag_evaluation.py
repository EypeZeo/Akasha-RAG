"""Private R0 evaluation-set validation and deterministic retrieval metrics.

This module has no dependency on Chroma, the database, or an LLM. It is the
pure-data layer for the local R0 evaluator so schema and metric behavior can be
tested without accessing real knowledge-base content or paid providers.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Mapping, Sequence

EVALUATION_SCHEMA_VERSION = 1
REPORT_SCHEMA_VERSION = 1
DEFAULT_CUTOFFS = (1, 3, 5, 8)
SUPPORTED_PLATFORMS = frozenset(("all", "douyin", "bilibili"))
SUPPORTED_CATEGORIES = frozenset(
    (
        "exact_fact",
        "numbers",
        "colloquial",
        "cross_item_synthesis",
        "scoped_collection",
        "scoped_platform",
        "multipart_video",
        "unanswerable",
        "stale_or_removed",
    )
)
SUPPORTED_ANSWERABILITY = frozenset(("answerable", "unanswerable", "invalidated"))
FILTER_REASONS = frozenset(
    (
        "scope",
        "platform",
        "inactive_item",
        "not_done",
        "deduplicated",
        "mmr",
        "top_k",
        "context_budget",
        "invalid_metadata",
        "error",
    )
)


class EvaluationError(ValueError):
    """Raised when an evaluation input violates the versioned R0 contract."""


def _error(path: str, message: str) -> EvaluationError:
    return EvaluationError(f"{path}: {message}")


def _mapping(value: object, path: str) -> dict:
    if not isinstance(value, dict):
        raise _error(path, "must be an object")
    return value


def _strict_keys(value: dict, path: str, required: set[str], optional: set[str] = set()) -> None:
    keys = set(value)
    missing = sorted(required - keys)
    unknown = sorted(keys - required - optional)
    if missing:
        raise _error(path, f"missing field(s): {', '.join(missing)}")
    if unknown:
        raise _error(path, f"unknown field(s): {', '.join(unknown)}")


def _string(value: object, path: str, *, max_length: int = 512, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise _error(path, "must be a string")
    if not allow_empty and not value.strip():
        raise _error(path, "must not be empty")
    if len(value) > max_length:
        raise _error(path, f"must be at most {max_length} characters")
    return value


def _optional_string(value: object, path: str, *, max_length: int = 512) -> str | None:
    if value is None:
        return None
    return _string(value, path, max_length=max_length)


def _list(value: object, path: str) -> list:
    if not isinstance(value, list):
        raise _error(path, "must be an array")
    return value


def _integer(value: object, path: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _error(path, "must be an integer")
    if value < minimum or value > maximum:
        raise _error(path, f"must be between {minimum} and {maximum}")
    return value


def _number(value: object, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _error(path, "must be a finite number")
    return float(value)


@dataclass(frozen=True)
class ScopedItem:
    platform: str
    platform_item_id: str

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.platform_item_id}"


@dataclass(frozen=True)
class GoldItem:
    platform: str
    platform_item_id: str
    relevance: int

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.platform_item_id}"


@dataclass(frozen=True)
class GoldChunk:
    chunk_id: str
    relevance: int


@dataclass(frozen=True)
class RequiredClaim:
    claim_id: str
    text: str
    supporting_chunks: tuple[str, ...]


@dataclass(frozen=True)
class EvaluationScope:
    collection_id: str | None
    platform: str
    expected_scope_items: tuple[ScopedItem, ...]


@dataclass(frozen=True)
class EvaluationGold:
    relevant_items: tuple[GoldItem, ...]
    relevant_chunks: tuple[GoldChunk, ...]
    required_claims: tuple[RequiredClaim, ...]
    expected_answer: str | None
    no_basis_reason: str | None


@dataclass(frozen=True)
class EvaluationCase:
    schema_version: int
    case_id: str
    language: str
    category: str
    question: str
    scope: EvaluationScope
    answerability: str
    gold: EvaluationGold
    notes: str | None


@dataclass(frozen=True)
class EvaluationDataset:
    schema_version: int
    cases: tuple[EvaluationCase, ...]
    sha256: str

    @property
    def case_ids(self) -> frozenset[str]:
        return frozenset(case.case_id for case in self.cases)


@dataclass(frozen=True)
class RetrievalCandidate:
    chunk_id: str
    platform: str
    platform_item_id: str
    score: float

    @property
    def item_key(self) -> str:
        return f"{self.platform}:{self.platform_item_id}"


@dataclass(frozen=True)
class RetrievalObservation:
    candidates: tuple[RetrievalCandidate, ...]
    final_context_chunk_ids: tuple[str, ...]


def _parse_scoped_item(value: object, path: str) -> ScopedItem:
    item = _mapping(value, path)
    _strict_keys(item, path, {"platform", "platform_item_id"})
    platform = _string(item["platform"], f"{path}.platform", max_length=16)
    if platform not in SUPPORTED_PLATFORMS - {"all"}:
        raise _error(f"{path}.platform", "must be douyin or bilibili")
    platform_item_id = _string(item["platform_item_id"], f"{path}.platform_item_id", max_length=256)
    return ScopedItem(platform, platform_item_id)


def _parse_gold_item(value: object, path: str) -> GoldItem:
    item = _mapping(value, path)
    _strict_keys(item, path, {"platform", "platform_item_id", "relevance"})
    scoped = _parse_scoped_item(
        {"platform": item["platform"], "platform_item_id": item["platform_item_id"]}, path
    )
    relevance = _integer(item["relevance"], f"{path}.relevance", minimum=0, maximum=3)
    return GoldItem(scoped.platform, scoped.platform_item_id, relevance)


def _parse_gold_chunk(value: object, path: str) -> GoldChunk:
    item = _mapping(value, path)
    _strict_keys(item, path, {"chunk_id", "relevance"})
    chunk_id = _string(item["chunk_id"], f"{path}.chunk_id", max_length=512)
    relevance = _integer(item["relevance"], f"{path}.relevance", minimum=0, maximum=3)
    return GoldChunk(chunk_id, relevance)


def _parse_case(raw: object, line_number: int) -> EvaluationCase:
    path = f"line {line_number}"
    value = _mapping(raw, path)
    _strict_keys(
        value,
        path,
        {"schema_version", "case_id", "language", "category", "question", "scope", "answerability", "gold"},
        {"notes"},
    )
    schema_version = _integer(value["schema_version"], f"{path}.schema_version", minimum=1, maximum=1)
    case_id = _string(value["case_id"], f"{path}.case_id", max_length=128)
    language = _string(value["language"], f"{path}.language", max_length=32)
    category = _string(value["category"], f"{path}.category", max_length=64)
    if category not in SUPPORTED_CATEGORIES:
        raise _error(f"{path}.category", f"must be one of {sorted(SUPPORTED_CATEGORIES)}")
    question = _string(value["question"], f"{path}.question", max_length=50000)
    answerability = _string(value["answerability"], f"{path}.answerability", max_length=32)
    if answerability not in SUPPORTED_ANSWERABILITY:
        raise _error(f"{path}.answerability", f"must be one of {sorted(SUPPORTED_ANSWERABILITY)}")

    scope_value = _mapping(value["scope"], f"{path}.scope")
    _strict_keys(scope_value, f"{path}.scope", {"collection_id", "platform", "expected_scope_items"})
    collection_id = _optional_string(scope_value["collection_id"], f"{path}.scope.collection_id", max_length=64)
    platform = _string(scope_value["platform"], f"{path}.scope.platform", max_length=16)
    if platform not in SUPPORTED_PLATFORMS:
        raise _error(f"{path}.scope.platform", f"must be one of {sorted(SUPPORTED_PLATFORMS)}")
    expected_items: list[ScopedItem] = []
    seen_scope_items: set[str] = set()
    for index, item_value in enumerate(_list(scope_value["expected_scope_items"], f"{path}.scope.expected_scope_items")):
        item = _parse_scoped_item(item_value, f"{path}.scope.expected_scope_items[{index}]")
        if platform != "all" and item.platform != platform:
            raise _error(
                f"{path}.scope.expected_scope_items[{index}].platform",
                "must match the scope platform unless scope.platform is all",
            )
        if item.key in seen_scope_items:
            raise _error(f"{path}.scope.expected_scope_items[{index}]", "duplicates another scope item")
        seen_scope_items.add(item.key)
        expected_items.append(item)

    gold_value = _mapping(value["gold"], f"{path}.gold")
    _strict_keys(
        gold_value,
        f"{path}.gold",
        {"relevant_items", "relevant_chunks", "required_claims", "expected_answer", "no_basis_reason"},
    )
    relevant_items: list[GoldItem] = []
    seen_items: set[str] = set()
    for index, item_value in enumerate(_list(gold_value["relevant_items"], f"{path}.gold.relevant_items")):
        item = _parse_gold_item(item_value, f"{path}.gold.relevant_items[{index}]")
        if item.key in seen_items:
            raise _error(f"{path}.gold.relevant_items[{index}]", "duplicates another relevant item")
        seen_items.add(item.key)
        relevant_items.append(item)

    relevant_chunks: list[GoldChunk] = []
    seen_chunks: set[str] = set()
    for index, chunk_value in enumerate(_list(gold_value["relevant_chunks"], f"{path}.gold.relevant_chunks")):
        chunk = _parse_gold_chunk(chunk_value, f"{path}.gold.relevant_chunks[{index}]")
        if chunk.chunk_id in seen_chunks:
            raise _error(f"{path}.gold.relevant_chunks[{index}]", "duplicates another relevant chunk")
        seen_chunks.add(chunk.chunk_id)
        relevant_chunks.append(chunk)

    claims: list[RequiredClaim] = []
    seen_claims: set[str] = set()
    for index, claim_value in enumerate(_list(gold_value["required_claims"], f"{path}.gold.required_claims")):
        claim_path = f"{path}.gold.required_claims[{index}]"
        claim = _mapping(claim_value, claim_path)
        _strict_keys(claim, claim_path, {"claim_id", "text", "supporting_chunks"})
        claim_id = _string(claim["claim_id"], f"{claim_path}.claim_id", max_length=128)
        if claim_id in seen_claims:
            raise _error(f"{claim_path}.claim_id", "duplicates another claim")
        seen_claims.add(claim_id)
        text = _string(claim["text"], f"{claim_path}.text", max_length=10000)
        supporting = _list(claim["supporting_chunks"], f"{claim_path}.supporting_chunks")
        if not supporting:
            raise _error(f"{claim_path}.supporting_chunks", "must contain at least one chunk")
        supporting_chunks = tuple(
            _string(chunk_id, f"{claim_path}.supporting_chunks[{chunk_index}]", max_length=512)
            for chunk_index, chunk_id in enumerate(supporting)
        )
        if len(set(supporting_chunks)) != len(supporting_chunks):
            raise _error(f"{claim_path}.supporting_chunks", "contains duplicate chunk IDs")
        missing = sorted(set(supporting_chunks) - seen_chunks)
        if missing:
            raise _error(f"{claim_path}.supporting_chunks", f"unknown gold chunk(s): {', '.join(missing)}")
        claims.append(RequiredClaim(claim_id, text, supporting_chunks))

    expected_answer = _optional_string(gold_value["expected_answer"], f"{path}.gold.expected_answer", max_length=50000)
    no_basis_reason = _optional_string(gold_value["no_basis_reason"], f"{path}.gold.no_basis_reason", max_length=2000)
    if answerability == "answerable":
        if not relevant_items:
            raise _error(f"{path}.gold.relevant_items", "answerable cases need at least one relevant item")
        if not expected_answer:
            raise _error(f"{path}.gold.expected_answer", "answerable cases need an expected answer")
    elif answerability == "unanswerable":
        if relevant_items or relevant_chunks or claims:
            raise _error(path, "unanswerable cases cannot have relevant items, chunks, or required claims")
        if not no_basis_reason:
            raise _error(f"{path}.gold.no_basis_reason", "unanswerable cases need a reason")
        if expected_answer:
            raise _error(f"{path}.gold.expected_answer", "unanswerable cases must not have an answer")

    notes = _optional_string(value.get("notes"), f"{path}.notes", max_length=5000)
    return EvaluationCase(
        schema_version=schema_version,
        case_id=case_id,
        language=language,
        category=category,
        question=question,
        scope=EvaluationScope(collection_id, platform, tuple(expected_items)),
        answerability=answerability,
        gold=EvaluationGold(
            tuple(relevant_items), tuple(relevant_chunks), tuple(claims), expected_answer, no_basis_reason
        ),
        notes=notes,
    )


def load_dataset(path: str | Path) -> EvaluationDataset:
    """Load and validate a private R0 JSONL dataset, returning its byte hash."""
    dataset_path = Path(path)
    try:
        raw = dataset_path.read_bytes()
    except OSError as exc:
        raise EvaluationError(f"cannot read dataset {dataset_path}: {exc}") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvaluationError(f"dataset {dataset_path} is not valid UTF-8") from exc

    cases: list[EvaluationCase] = []
    seen: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw_case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvaluationError(f"line {line_number}: invalid JSON: {exc.msg}") from exc
        case = _parse_case(raw_case, line_number)
        if case.case_id in seen:
            raise EvaluationError(f"line {line_number}.case_id: duplicate case ID {case.case_id!r}")
        seen.add(case.case_id)
        cases.append(case)
    if not cases:
        raise EvaluationError("dataset must contain at least one case")
    cases.sort(key=lambda item: item.case_id)
    return EvaluationDataset(EVALUATION_SCHEMA_VERSION, tuple(cases), hashlib.sha256(raw).hexdigest())


def _parse_candidate(value: object, path: str) -> RetrievalCandidate:
    candidate = _mapping(value, path)
    _strict_keys(candidate, path, {"chunk_id", "platform", "platform_item_id", "score"})
    chunk_id = _string(candidate["chunk_id"], f"{path}.chunk_id", max_length=512)
    platform = _string(candidate["platform"], f"{path}.platform", max_length=16)
    if platform not in SUPPORTED_PLATFORMS - {"all"}:
        raise _error(f"{path}.platform", "must be douyin or bilibili")
    platform_item_id = _string(candidate["platform_item_id"], f"{path}.platform_item_id", max_length=256)
    score = _number(candidate["score"], f"{path}.score")
    return RetrievalCandidate(chunk_id, platform, platform_item_id, score)


def load_observations(path: str | Path) -> dict[str, RetrievalObservation]:
    """Load retrieval-only observations without accepting source text fields."""
    observation_path = Path(path)
    try:
        raw = observation_path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvaluationError(f"invalid observations file {observation_path}: {exc}") from exc
    root = _mapping(value, "observations")
    observations: dict[str, RetrievalObservation] = {}
    for case_id, raw_observation in root.items():
        case_path = f"observations[{case_id!r}]"
        _string(case_id, case_path, max_length=128)
        observation = _mapping(raw_observation, case_path)
        _strict_keys(observation, case_path, {"candidates", "final_context_chunk_ids"})
        candidates: list[RetrievalCandidate] = []
        seen_chunks: set[str] = set()
        for index, raw_candidate in enumerate(_list(observation["candidates"], f"{case_path}.candidates")):
            candidate = _parse_candidate(raw_candidate, f"{case_path}.candidates[{index}]")
            if candidate.chunk_id in seen_chunks:
                raise _error(f"{case_path}.candidates[{index}].chunk_id", "duplicates another candidate")
            seen_chunks.add(candidate.chunk_id)
            candidates.append(candidate)
        context_ids = tuple(
            _string(chunk_id, f"{case_path}.final_context_chunk_ids[{index}]", max_length=512)
            for index, chunk_id in enumerate(
                _list(observation["final_context_chunk_ids"], f"{case_path}.final_context_chunk_ids")
            )
        )
        if len(set(context_ids)) != len(context_ids):
            raise _error(f"{case_path}.final_context_chunk_ids", "contains duplicate chunk IDs")
        observations[case_id] = RetrievalObservation(tuple(candidates), context_ids)
    return observations


def _ranked_unique(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def recall_at_k(ranked_ids: Sequence[str], relevant_ids: set[str], k: int) -> float | None:
    """Return recall for a ranked list, or None when the case has no gold positives."""
    if not relevant_ids:
        return None
    if k < 1:
        raise ValueError("k must be positive")
    return len(set(_ranked_unique(ranked_ids)[:k]) & relevant_ids) / len(relevant_ids)


def mean_reciprocal_rank(ranked_ids: Sequence[str], relevant_ids: set[str]) -> float | None:
    """Return reciprocal rank of the first relevant result."""
    if not relevant_ids:
        return None
    for rank, value in enumerate(_ranked_unique(ranked_ids), start=1):
        if value in relevant_ids:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(ranked_ids: Sequence[str], relevance: Mapping[str, int], k: int) -> float | None:
    """Return graded nDCG@k using the R0 relevance scale."""
    if k < 1:
        raise ValueError("k must be positive")
    positive = {key: grade for key, grade in relevance.items() if grade > 0}
    if not positive:
        return None
    ranked = _ranked_unique(ranked_ids)[:k]

    def gain(grade: int, rank: int) -> float:
        return (2**grade - 1) / math.log2(rank + 1)

    dcg = sum(gain(positive.get(value, 0), rank) for rank, value in enumerate(ranked, start=1))
    ideal = sorted(positive.values(), reverse=True)[:k]
    idcg = sum(gain(grade, rank) for rank, grade in enumerate(ideal, start=1))
    return dcg / idcg if idcg else None


def _rank_metrics(ranked_ids: Sequence[str], relevance: Mapping[str, int], cutoffs: Sequence[int]) -> dict[str, float | None]:
    relevant = {key for key, grade in relevance.items() if grade > 0}
    result: dict[str, float | None] = {
        f"recall@{k}": recall_at_k(ranked_ids, relevant, k) for k in cutoffs
    }
    result["mrr"] = mean_reciprocal_rank(ranked_ids, relevant)
    result.update({f"ndcg@{k}": ndcg_at_k(ranked_ids, relevance, k) for k in cutoffs})
    return result


def _rounded(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def _round_metrics(metrics: Mapping[str, float | None]) -> dict[str, float | None]:
    return {key: _rounded(value) for key, value in metrics.items()}


def case_retrieval_metrics(
    case: EvaluationCase,
    observation: RetrievalObservation,
    cutoffs: Sequence[int] = DEFAULT_CUTOFFS,
) -> dict:
    """Calculate chunk, item, and final-context metrics for one case."""
    if not cutoffs or any(isinstance(k, bool) or not isinstance(k, int) or k < 1 for k in cutoffs):
        raise ValueError("cutoffs must contain positive integers")
    candidates = observation.candidates
    chunk_ids = [candidate.chunk_id for candidate in candidates]
    item_keys = [candidate.item_key for candidate in candidates]
    chunk_relevance = {chunk.chunk_id: chunk.relevance for chunk in case.gold.relevant_chunks}
    item_relevance = {item.key: item.relevance for item in case.gold.relevant_items}
    context_relevance = {key: grade for key, grade in chunk_relevance.items() if grade > 0}
    return {
        "case_id": case.case_id,
        "category": case.category,
        "answerability": case.answerability,
        "candidate_count": len(candidates),
        "context_chunk_count": len(observation.final_context_chunk_ids),
        "chunk": _round_metrics(_rank_metrics(chunk_ids, chunk_relevance, cutoffs)) if chunk_relevance else None,
        "item": _round_metrics(_rank_metrics(item_keys, item_relevance, cutoffs)) if item_relevance else None,
        "context": _round_metrics({
            f"recall@{k}": recall_at_k(observation.final_context_chunk_ids, set(context_relevance), k)
            for k in cutoffs
        }) if context_relevance else None,
    }


def _aggregate_group(case_results: Sequence[dict], group: str) -> dict | None:
    available = [result[group] for result in case_results if result[group] is not None]
    if not available:
        return None
    keys = sorted({key for metrics in available for key in metrics})
    return {
        key: _rounded(sum(metrics[key] for metrics in available if metrics[key] is not None) / sum(
            metrics[key] is not None for metrics in available
        ))
        for key in keys
    }


def retrieval_report(
    dataset: EvaluationDataset,
    observations: Mapping[str, RetrievalObservation],
    cutoffs: Sequence[int] = DEFAULT_CUTOFFS,
) -> dict:
    """Build a deterministic retrieval-only report from a validated dataset."""
    dataset_ids = dataset.case_ids
    unknown = sorted(set(observations) - dataset_ids)
    if unknown:
        raise EvaluationError(f"observations contain unknown case ID(s): {', '.join(unknown)}")
    required_ids = {case.case_id for case in dataset.cases if case.answerability != "invalidated"}
    missing = sorted(required_ids - set(observations))
    if missing:
        raise EvaluationError(f"observations are missing case ID(s): {', '.join(missing)}")
    case_results = [
        case_retrieval_metrics(case, observations[case.case_id], cutoffs)
        for case in dataset.cases
        if case.answerability != "invalidated"
    ]
    categories = Counter(case.category for case in dataset.cases)
    answerability = Counter(case.answerability for case in dataset.cases)
    by_category = {
        category: {
            "case_count": sum(result["category"] == category for result in case_results),
            "chunk": _aggregate_group([result for result in case_results if result["category"] == category], "chunk"),
            "item": _aggregate_group([result for result in case_results if result["category"] == category], "item"),
            "context": _aggregate_group([result for result in case_results if result["category"] == category], "context"),
        }
        for category in sorted(categories)
        if category in {result["category"] for result in case_results}
    }
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "report_type": "r0-retrieval",
        "dataset_schema_version": dataset.schema_version,
        "dataset_sha256": dataset.sha256,
        "cutoffs": list(cutoffs),
        "case_counts": {
            "total": len(dataset.cases),
            "valid": len(case_results),
            "invalidated": answerability["invalidated"],
            "by_category": dict(sorted(categories.items())),
            "by_answerability": dict(sorted(answerability.items())),
        },
        "overall": {
            "chunk": _aggregate_group(case_results, "chunk"),
            "item": _aggregate_group(case_results, "item"),
            "context": _aggregate_group(case_results, "context"),
        },
        "by_category": by_category,
        "cases": case_results,
    }
