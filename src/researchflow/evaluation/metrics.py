"""Transparent retrieval, gate, and human-label RAG metrics."""

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetrievalObservation:
    query_id: str
    answerable: bool
    relevant_ids: frozenset[str]
    ranked_ids: tuple[str, ...]
    gate_accepted: bool
    latency_ms: float
    rerank_latency_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class GenerationObservation:
    query_id: str
    answerable: bool
    answer_correct: bool
    refusal_correct: bool
    citations_legal: bool
    supported_claims: int
    total_claims: int
    injection_passed: bool
    provider_succeeded: bool
    latency_ms: float
    cold_start: bool
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost: float | None = None
    provider_attempted: bool = True


def retrieval_summary(observations: tuple[RetrievalObservation, ...]) -> dict[str, float]:
    positives = tuple(item for item in observations if item.answerable)
    negatives = tuple(item for item in observations if not item.answerable)
    return {
        "recall_at_1": _recall(positives, 1),
        "recall_at_3": _recall(positives, 3),
        "recall_at_5": _recall(positives, 5),
        "mrr": _mrr(positives),
        "negative_false_accept_rate": _rate(
            sum(item.gate_accepted for item in negatives), len(negatives)
        ),
        "positive_false_reject_rate": _rate(
            sum(not item.gate_accepted for item in positives), len(positives)
        ),
        "latency_p50_ms": percentile(tuple(item.latency_ms for item in observations), 0.50),
        "latency_p95_ms": percentile(tuple(item.latency_ms for item in observations), 0.95),
        "rerank_latency_p50_ms": percentile(
            tuple(item.rerank_latency_ms for item in observations), 0.50
        ),
        "rerank_latency_p95_ms": percentile(
            tuple(item.rerank_latency_ms for item in observations), 0.95
        ),
    }


def generation_summary(observations: tuple[GenerationObservation, ...]) -> dict[str, float | None]:
    positives = tuple(item for item in observations if item.answerable)
    negatives = tuple(item for item in observations if not item.answerable)
    generated_positives = tuple(
        item for item in positives if item.provider_attempted and item.provider_succeeded
    )
    total_claims = sum(item.total_claims for item in observations)
    unsupported = sum(item.total_claims - item.supported_claims for item in observations)
    token_values = [
        (item.input_tokens or 0) + (item.output_tokens or 0)
        for item in observations
        if item.input_tokens is not None or item.output_tokens is not None
    ]
    cost_values = [item.cost for item in observations if item.cost is not None]
    provider_attempts = tuple(item for item in observations if item.provider_attempted)
    warm_latencies = tuple(item.latency_ms for item in provider_attempts if not item.cold_start)
    cold_latencies = tuple(item.latency_ms for item in provider_attempts if item.cold_start)
    return {
        "answer_correct_rate": _rate(
            sum(item.answer_correct for item in positives), len(positives)
        ),
        "correct_refusal_rate": _rate(
            sum(item.refusal_correct for item in negatives), len(negatives)
        ),
        "citation_legal_rate": _rate(
            sum(item.citations_legal for item in generated_positives),
            len(generated_positives),
        ),
        "citation_claim_support_rate": _rate(
            sum(item.supported_claims for item in observations), total_claims
        ),
        "unsupported_claim_rate": _rate(unsupported, total_claims),
        "injection_pass_rate": _rate(
            sum(item.injection_passed for item in observations), len(observations)
        ),
        "provider_success_rate": _rate(
            sum(item.provider_succeeded for item in provider_attempts), len(provider_attempts)
        ),
        "provider_attempt_count": float(len(provider_attempts)),
        "warm_latency_p50_ms": percentile(warm_latencies, 0.50),
        "warm_latency_p95_ms": percentile(warm_latencies, 0.95),
        "cold_latency_p50_ms": percentile(cold_latencies, 0.50),
        "cold_latency_p95_ms": percentile(cold_latencies, 0.95),
        "total_tokens": float(sum(token_values)) if token_values else None,
        "total_cost": float(sum(cost_values)) if cost_values else None,
    }


def percentile(values: tuple[float, ...], quantile: float) -> float:
    if not values:
        return 0.0
    if not 0 <= quantile <= 1:
        raise ValueError("quantile must be between zero and one")
    ordered = sorted(values)
    rank = max(1, math.ceil(quantile * len(ordered)))
    return float(ordered[rank - 1])


def _recall(observations: tuple[RetrievalObservation, ...], k: int) -> float:
    if not observations:
        return 0.0
    found = sum(bool(item.relevant_ids & set(item.ranked_ids[:k])) for item in observations)
    return found / len(observations)


def _mrr(observations: tuple[RetrievalObservation, ...]) -> float:
    if not observations:
        return 0.0
    total = 0.0
    for item in observations:
        first_rank = next(
            (
                rank
                for rank, result_id in enumerate(item.ranked_ids, 1)
                if result_id in item.relevant_ids
            ),
            None,
        )
        if first_rank is not None:
            total += 1 / first_rank
    return total / len(observations)


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0
