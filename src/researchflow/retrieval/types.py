"""Typed retrieval values independent of FastAPI schemas."""

from dataclasses import dataclass, replace
from typing import Literal
from uuid import UUID

from researchflow.providers.embedding import EmbeddingModelInfo

SearchMode = Literal["vector", "keyword", "hybrid"]
SearchStatus = Literal["ok", "no_indexed_documents"]


@dataclass(frozen=True, slots=True)
class RetrievalHit:
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
    retrieval_method: SearchMode
    vector_distance: float | None = None
    vector_rank: int | None = None
    keyword_score: float | None = None
    keyword_rank: int | None = None
    rrf_score: float | None = None
    original_rank: int | None = None
    rerank_score: float | None = None
    rerank_rank: int | None = None

    def with_updates(self, **changes: object) -> "RetrievalHit":
        return replace(self, **changes)


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    status: SearchStatus
    mode: SearchMode
    items: tuple[RetrievalHit, ...]
    model: EmbeddingModelInfo
    rrf_k: int | None = None
