"""Provider-neutral reranking values."""

from typing import Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from researchflow.retrieval.types import RetrievalHit


class RerankerInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    max_input_tokens: int = Field(gt=0)


class RerankCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    segment_id: UUID
    content: str = Field(min_length=1)
    original_rank: int = Field(ge=1)


class RerankRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    query: str = Field(min_length=1)
    candidates: tuple[RerankCandidate, ...] = Field(min_length=1)


class RerankScore(BaseModel):
    model_config = ConfigDict(frozen=True)

    segment_id: UUID
    score: float


class RerankResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    scores: tuple[RerankScore, ...]
    model: RerankerInfo


@runtime_checkable
class RerankerProvider(Protocol):
    name: str
    info: RerankerInfo
    max_batch_size: int

    async def score(self, request: RerankRequest, *, timeout_seconds: float) -> RerankResponse: ...


class RerankResult(BaseModel):
    """Metadata returned by the service; hits remain domain dataclasses."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    items: tuple[RetrievalHit, ...]
    model: RerankerInfo
    duration_ms: float = Field(ge=0)
