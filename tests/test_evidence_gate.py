"""Evidence sufficiency and conservative refusal behavior."""

from researchflow.rag.evidence import EvidenceGate
from researchflow.rag.types import EvidenceReason, EvidenceStatus
from tests.support import make_retrieval_hit


def gate(*, score: float = 0.5, coverage: float = 0.5) -> EvidenceGate:
    return EvidenceGate(min_rerank_score=score, min_query_coverage=coverage, max_items=3)


def test_answerable_evidence_is_selected() -> None:
    hit = make_retrieval_hit("A toy shelf ledger lists two empty slots.", rerank_score=1.2)
    result = gate().assess("Which ledger keeps the toy shelf slot labels?", (hit,))
    assert result.status is EvidenceStatus.ANSWERABLE
    assert result.selected == (hit,)
    assert result.reasons == ()


def test_empty_candidates_are_insufficient() -> None:
    result = gate().assess("question", ())
    assert result.status is EvidenceStatus.INSUFFICIENT_EVIDENCE
    assert result.reasons == (EvidenceReason.NO_CANDIDATES,)


def test_unreranked_candidates_are_not_treated_as_calibrated() -> None:
    result = gate().assess("toy shelf ledger", (make_retrieval_hit(),))
    assert result.reasons == (EvidenceReason.RERANK_REQUIRED,)


def test_score_below_threshold_is_rejected() -> None:
    hit = make_retrieval_hit("toy shelf ledger", rerank_score=0.49)
    result = gate().assess("toy shelf ledger", (hit,))
    assert result.reasons == (EvidenceReason.SCORE_BELOW_THRESHOLD,)
    assert result.top_rerank_score == 0.49


def test_score_threshold_boundary_is_inclusive() -> None:
    hit = make_retrieval_hit("toy shelf ledger", rerank_score=0.5)
    result = gate().assess("toy shelf ledger", (hit,))
    assert result.status is EvidenceStatus.ANSWERABLE


def test_low_query_coverage_is_rejected() -> None:
    hit = make_retrieval_hit("an invented orange kite entry", rerank_score=2.0)
    result = gate(coverage=0.6).assess("puzzle tray square count", (hit,))
    assert result.reasons == (EvidenceReason.QUERY_COVERAGE_LOW,)


def test_explicit_numeric_contradiction_is_rejected() -> None:
    first = make_retrieval_hit("puzzle tile count is 6", rerank_score=2.0)
    second = make_retrieval_hit("puzzle tile count is 9", rerank_score=1.9)
    result = gate(coverage=0.5).assess("puzzle tile count", (first, second))
    assert result.reasons == (EvidenceReason.CONTRADICTORY_EVIDENCE,)
