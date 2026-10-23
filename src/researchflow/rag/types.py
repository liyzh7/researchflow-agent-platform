"""Typed RAG states, generated payloads, and public-domain results."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from researchflow.retrieval.types import RetrievalHit, SearchMode


class EvidenceStatus(StrEnum):
    ANSWERABLE = "ANSWERABLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    RETRIEVAL_UNAVAILABLE = "RETRIEVAL_UNAVAILABLE"


class EvidenceReason(StrEnum):
    NO_CANDIDATES = "NO_CANDIDATES"
    RERANK_REQUIRED = "RERANK_REQUIRED"
    SCORE_BELOW_THRESHOLD = "SCORE_BELOW_THRESHOLD"
    QUERY_COVERAGE_LOW = "QUERY_COVERAGE_LOW"
    CONTRADICTORY_EVIDENCE = "CONTRADICTORY_EVIDENCE"
    CONTEXT_BUDGET_EXCEEDED = "CONTEXT_BUDGET_EXCEEDED"
    RETRIEVAL_FAILED = "RETRIEVAL_FAILED"


@dataclass(frozen=True, slots=True)
class EvidenceGateResult:
    status: EvidenceStatus
    reasons: tuple[EvidenceReason, ...]
    selected: tuple[RetrievalHit, ...]
    top_rerank_score: float | None
    query_coverage: float


@dataclass(frozen=True, slots=True)
class PromptEvidence:
    evidence_id: str
    hit: RetrievalHit


@dataclass(frozen=True, slots=True)
class PromptPackage:
    system_prompt: str
    user_prompt: str
    evidence: tuple[PromptEvidence, ...]
    estimated_context_tokens: int
    response_schema: dict[str, object]


class GeneratedCitation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(min_length=2, max_length=16)


class GroundedAnswerPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    answer: str = Field(min_length=1, max_length=10000)
    citations: tuple[GeneratedCitation, ...] = Field(min_length=1, max_length=20)


class VerifiedCitation(BaseModel):
    model_config = ConfigDict(frozen=True)

    evidence_id: str
    document_id: UUID
    document_key: str
    version_id: UUID
    version_number: int
    chunk_id: UUID
    chunk_index: int
    segment_id: UUID
    segment_index: int
    char_start: int
    char_end: int
    line_start: int
    line_end: int
    excerpt: str


@dataclass(frozen=True, slots=True)
class RAGTiming:
    retrieval_ms: float = 0.0
    rerank_ms: float = 0.0
    gate_ms: float = 0.0
    generation_ms: float = 0.0
    citation_validation_ms: float = 0.0
    total_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class RAGAnswerResult:
    answer: str
    status: str
    is_mock: bool
    provider_name: str | None
    provider_model: str | None
    citations: tuple[VerifiedCitation, ...]
    retrieval_mode: SearchMode
    rerank_enabled: bool
    reranker_model: str | None
    reranker_revision: str | None
    evidence_status: EvidenceStatus
    evidence_reasons: tuple[EvidenceReason, ...]
    top_rerank_score: float | None
    query_coverage: float
    timing: RAGTiming
    input_tokens: int | None = None
    output_tokens: int | None = None
