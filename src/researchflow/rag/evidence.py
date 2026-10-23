"""Conservative evidence sufficiency gate with inspectable reasons."""

import re
from collections.abc import Sequence

from researchflow.rag.types import EvidenceGateResult, EvidenceReason, EvidenceStatus
from researchflow.retrieval.types import RetrievalHit

_TOKEN = re.compile(r"[A-Za-z0-9_]+|[\u3400-\u9fff]", re.UNICODE)
_NUMBER_ASSERTION = re.compile(
    r"([A-Za-z][A-Za-z0-9 _-]{1,30}|[\u3400-\u9fff]{1,12})\s*(?:is|=|为|:|\uff1a)\s*"
    r"(-?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_NEGATIVE_MARKERS = ("does not support", "not supported", "不支持", "并非", "不是")
_POSITIVE_MARKERS = ("supports", "is supported", "支持", "是")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "is",
    "of",
    "the",
    "to",
    "what",
    "which",
    "中",
    "为",
    "是",
    "的",
}


class EvidenceGate:
    """Apply a locally calibrated logit threshold plus observable coverage checks."""

    def __init__(
        self,
        *,
        min_rerank_score: float,
        min_query_coverage: float,
        max_items: int,
    ) -> None:
        self._min_rerank_score = min_rerank_score
        self._min_query_coverage = min_query_coverage
        self._max_items = max_items

    def assess(self, query: str, candidates: Sequence[RetrievalHit]) -> EvidenceGateResult:
        if not candidates:
            return self._result(EvidenceReason.NO_CANDIDATES)
        if any(item.rerank_score is None for item in candidates):
            return self._result(EvidenceReason.RERANK_REQUIRED)

        relevant = tuple(
            item for item in candidates if float(item.rerank_score) >= self._min_rerank_score
        )[: self._max_items]
        top_score = max(float(item.rerank_score) for item in candidates)
        if not relevant:
            return self._result(
                EvidenceReason.SCORE_BELOW_THRESHOLD,
                top_rerank_score=top_score,
            )

        coverage = _query_coverage(query, relevant)
        if coverage < self._min_query_coverage:
            return self._result(
                EvidenceReason.QUERY_COVERAGE_LOW,
                top_rerank_score=top_score,
                query_coverage=coverage,
            )
        if _has_explicit_contradiction(query, relevant):
            return self._result(
                EvidenceReason.CONTRADICTORY_EVIDENCE,
                top_rerank_score=top_score,
                query_coverage=coverage,
            )
        return EvidenceGateResult(
            status=EvidenceStatus.ANSWERABLE,
            reasons=(),
            selected=relevant,
            top_rerank_score=top_score,
            query_coverage=coverage,
        )

    @staticmethod
    def _result(
        reason: EvidenceReason,
        *,
        top_rerank_score: float | None = None,
        query_coverage: float = 0.0,
    ) -> EvidenceGateResult:
        return EvidenceGateResult(
            status=EvidenceStatus.INSUFFICIENT_EVIDENCE,
            reasons=(reason,),
            selected=(),
            top_rerank_score=top_rerank_score,
            query_coverage=query_coverage,
        )


def _terms(value: str) -> set[str]:
    return {
        token.casefold() for token in _TOKEN.findall(value) if token.casefold() not in _STOPWORDS
    }


def _query_coverage(query: str, evidence: Sequence[RetrievalHit]) -> float:
    query_terms = _terms(query)
    if not query_terms:
        return 0.0
    evidence_terms: set[str] = set()
    for item in evidence:
        evidence_terms.update(_terms(item.content))
    return len(query_terms & evidence_terms) / len(query_terms)


def _has_explicit_contradiction(query: str, evidence: Sequence[RetrievalHit]) -> bool:
    if len(evidence) < 2:
        return False
    query_terms = _terms(query)
    assertions: dict[str, set[str]] = {}
    polarities: set[str] = set()
    for item in evidence:
        if query_terms and len(query_terms & _terms(item.content)) / len(query_terms) < 0.5:
            continue
        folded = item.content.casefold()
        for subject, value in _NUMBER_ASSERTION.findall(folded):
            key = " ".join(subject.split())
            assertions.setdefault(key, set()).add(value)
        if any(marker in folded for marker in _NEGATIVE_MARKERS):
            polarities.add("negative")
        if any(marker in folded for marker in _POSITIVE_MARKERS) and not any(
            marker in folded for marker in _NEGATIVE_MARKERS
        ):
            polarities.add("positive")
    return any(len(values) > 1 for values in assertions.values()) or len(polarities) > 1
