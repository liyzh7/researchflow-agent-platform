"""Deterministic evaluation math including cold/warm separation."""

from researchflow.evaluation.metrics import (
    GenerationObservation,
    RetrievalObservation,
    generation_summary,
    percentile,
    retrieval_summary,
)


def test_retrieval_metrics_keep_gate_errors_separate_from_recall() -> None:
    observations = (
        RetrievalObservation("p1", True, frozenset({"a"}), ("a", "b"), True, 10, 3),
        RetrievalObservation("p2", True, frozenset({"c"}), ("x", "c"), False, 20, 4),
        RetrievalObservation("n1", False, frozenset(), ("z",), True, 30, 5),
        RetrievalObservation("n2", False, frozenset(), (), False, 40, 6),
    )
    result = retrieval_summary(observations)
    assert result["recall_at_1"] == 0.5
    assert result["recall_at_3"] == 1.0
    assert result["mrr"] == 0.75
    assert result["negative_false_accept_rate"] == 0.5
    assert result["positive_false_reject_rate"] == 0.5
    assert result["latency_p95_ms"] == 40


def test_generation_metrics_do_not_equate_legal_citation_with_supported_claim() -> None:
    observations = (
        GenerationObservation(
            "p1", True, True, False, True, 1, 2, True, True, 100, False, 10, 5, 0.01
        ),
        GenerationObservation("n1", False, False, True, False, 0, 0, True, True, 500, True),
    )
    result = generation_summary(observations)
    assert result["answer_correct_rate"] == 1.0
    assert result["correct_refusal_rate"] == 1.0
    assert result["citation_legal_rate"] == 1.0
    assert result["citation_claim_support_rate"] == 0.5
    assert result["unsupported_claim_rate"] == 0.5
    assert result["warm_latency_p50_ms"] == 100
    assert result["cold_latency_p50_ms"] == 500
    assert result["total_tokens"] == 15
    assert result["total_cost"] == 0.01


def test_provider_success_rate_excludes_gate_refusals_without_a_model_call() -> None:
    observations = (
        GenerationObservation(
            "answered",
            True,
            True,
            False,
            True,
            1,
            1,
            True,
            True,
            100,
            False,
            provider_attempted=True,
        ),
        GenerationObservation(
            "gate-refusal",
            False,
            False,
            True,
            False,
            0,
            0,
            True,
            False,
            10,
            False,
            provider_attempted=False,
        ),
        GenerationObservation(
            "positive-gate-refusal",
            True,
            False,
            False,
            False,
            0,
            0,
            True,
            False,
            5,
            False,
            provider_attempted=False,
        ),
    )
    result = generation_summary(observations)
    assert result["provider_success_rate"] == 1.0
    assert result["provider_attempt_count"] == 1.0
    assert result["citation_legal_rate"] == 1.0
    assert result["warm_latency_p50_ms"] == 100


def test_percentile_uses_nearest_rank_and_validates_quantile() -> None:
    assert percentile((1, 2, 3, 4), 0.5) == 2
    assert percentile((1, 2, 3, 4), 0.95) == 4
