"""Public API request and response models."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from researchflow.providers.embedding import EmbeddingModelInfo
from researchflow.rag.types import EvidenceReason, EvidenceStatus


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["researchflow"] = "researchflow"


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    database: Literal["available", "unavailable", "available_pgvector_missing"]
    pgvector_version: str | None = None


class InfoResponse(BaseModel):
    project_name: str
    version: str
    environment: str
    status: Literal["operational"] = "operational"
    provider_mode: Literal["mock", "openai_compatible"] = "mock"
    rag_enabled: bool = False
    agent_enabled: bool = False
    upload_max_bytes: int = 2 * 1024 * 1024


class ChatRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    question: str = Field(strict=True, min_length=1, max_length=2000)

    @field_validator("question")
    @classmethod
    def reject_blank_question(cls, value: str) -> str:
        if not value:
            raise ValueError("question must not be blank")
        return value


class SourceCitation(BaseModel):
    """Future citation shape; stage 1 always returns an empty collection."""

    source_id: str
    title: str


class ChatResponse(BaseModel):
    answer: str
    provider_mode: Literal["mock", "openai_compatible"]
    provider_model: str
    is_mock: bool
    rag_enabled: Literal[False] = False
    agent_enabled: Literal[False] = False
    citations: list[SourceCitation] = Field(default_factory=list)


class IngestionResponse(BaseModel):
    document_id: UUID
    version_id: UUID
    document_key: str
    version_number: int
    sha256: str
    chunk_count: int
    is_duplicate: bool


class VersionResponse(BaseModel):
    id: UUID
    version_number: int
    sha256: str
    original_filename: str
    content_type: str
    file_size: int
    parser_version: str
    created_at: datetime
    chunk_count: int


class DocumentResponse(BaseModel):
    id: UUID
    document_key: str
    title: str
    created_at: datetime
    updated_at: datetime
    current_version: VersionResponse | None


class DocumentPageResponse(BaseModel):
    items: list[DocumentResponse]
    total: int
    offset: int
    limit: int


class ChunkResponse(BaseModel):
    id: UUID
    chunk_index: int
    content: str
    char_start: int
    char_end: int
    line_start: int
    line_end: int
    chunker_version: str


class RetrievalSearchRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(strict=True, min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=50)
    mode: Literal["vector", "keyword", "hybrid"] = "hybrid"
    document_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    document_id: UUID | None = None

    @field_validator("query")
    @classmethod
    def reject_blank_query(cls, value: str) -> str:
        if not value:
            raise ValueError("query must not be blank")
        return value

    @model_validator(mode="after")
    def reject_ambiguous_document_filter(self) -> "RetrievalSearchRequest":
        if self.document_key is not None and self.document_id is not None:
            raise ValueError("use either document_key or document_id, not both")
        return self


class RetrievalHitResponse(BaseModel):
    document_id: UUID
    document_key: str
    title: str
    version_id: UUID
    version_number: int
    chunk_id: UUID
    chunk_index: int
    segment_id: UUID
    segment_index: int
    content: str
    char_start: int
    char_end: int
    line_start: int
    line_end: int
    retrieval_method: Literal["vector", "keyword", "hybrid"]
    vector_distance: float | None = None
    vector_rank: int | None = None
    keyword_score: float | None = None
    keyword_rank: int | None = None
    rrf_score: float | None = None
    original_rank: int | None = None
    rerank_score: float | None = None
    rerank_rank: int | None = None


class RetrievalSearchResponse(BaseModel):
    status: Literal["ok", "no_indexed_documents"]
    mode: Literal["vector", "keyword", "hybrid"]
    items: list[RetrievalHitResponse]
    model: EmbeddingModelInfo
    rrf_k: int | None = None


class RAGAnswerRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(strict=True, min_length=1, max_length=1000)
    retrieval_mode: Literal["vector", "keyword", "hybrid"] = "hybrid"
    top_k: int = Field(default=5, ge=1, le=20)
    rerank_enabled: bool = True
    document_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    document_id: UUID | None = None

    @field_validator("query")
    @classmethod
    def reject_blank_rag_query(cls, value: str) -> str:
        if not value:
            raise ValueError("query must not be blank")
        return value

    @model_validator(mode="after")
    def reject_ambiguous_rag_filter(self) -> "RAGAnswerRequest":
        if self.document_key is not None and self.document_id is not None:
            raise ValueError("use either document_key or document_id, not both")
        return self


class RAGCitationResponse(BaseModel):
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


class RAGTimingResponse(BaseModel):
    retrieval_ms: float
    rerank_ms: float
    gate_ms: float
    generation_ms: float
    citation_validation_ms: float
    total_ms: float


class RAGAnswerResponse(BaseModel):
    answer: str
    status: Literal["answered", "refused"]
    is_mock: bool
    provider_name: str | None
    provider_model: str | None
    citations: list[RAGCitationResponse]
    retrieval_mode: Literal["vector", "keyword", "hybrid"]
    rerank_enabled: bool
    reranker_model: str | None
    reranker_revision: str | None
    evidence_status: EvidenceStatus
    evidence_reasons: list[EvidenceReason]
    top_rerank_score: float | None
    query_coverage: float
    input_tokens: int | None
    output_tokens: int | None
    timing: RAGTimingResponse
