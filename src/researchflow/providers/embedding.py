"""Provider-neutral contracts for dense text embeddings."""

import hashlib
import json
import math
from collections.abc import Sequence
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from researchflow.providers.errors import EmbeddingProviderInvalidVectorError

EmbeddingPurpose = Literal["query", "document"]


class EmbeddingModelInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    model_name: str
    revision: str
    dimension: int = Field(gt=0)
    normalized: bool
    query_prefix: str
    passage_prefix: str
    max_input_tokens: int = Field(gt=0)


class EmbeddingRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    texts: tuple[str, ...] = Field(min_length=1, max_length=128)


class EmbeddingResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    vectors: tuple[tuple[float, ...], ...]
    model: EmbeddingModelInfo
    purpose: EmbeddingPurpose


class DocumentTokenization(BaseModel):
    model_config = ConfigDict(frozen=True)

    offsets: tuple[tuple[int, int], ...]
    prefixed_token_count: int = Field(ge=0)
    max_input_tokens: int = Field(gt=0)


@runtime_checkable
class EmbeddingProvider(Protocol):
    name: str
    info: EmbeddingModelInfo
    max_batch_size: int

    async def embed_queries(
        self, request: EmbeddingRequest, *, timeout_seconds: float
    ) -> EmbeddingResponse: ...

    async def embed_documents(
        self, request: EmbeddingRequest, *, timeout_seconds: float
    ) -> EmbeddingResponse: ...

    async def tokenize_document(
        self, text: str, *, timeout_seconds: float
    ) -> DocumentTokenization: ...

    async def count_document_tokens(
        self, texts: tuple[str, ...], *, timeout_seconds: float
    ) -> tuple[int, ...]: ...


def embedding_config_key(info: EmbeddingModelInfo) -> str:
    serialized = json.dumps(info.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def validate_embedding_vectors(
    vectors: Sequence[Sequence[float]],
    info: EmbeddingModelInfo,
    *,
    expected_count: int,
) -> tuple[tuple[float, ...], ...]:
    """Enforce the provider-neutral shape, finiteness and normalization contract."""

    if len(vectors) != expected_count:
        raise EmbeddingProviderInvalidVectorError("embedding output count is invalid")
    output: list[tuple[float, ...]] = []
    for row in vectors:
        values = tuple(float(value) for value in row)
        if len(values) != info.dimension:
            raise EmbeddingProviderInvalidVectorError("embedding output dimension is invalid")
        if any(not math.isfinite(value) for value in values):
            raise EmbeddingProviderInvalidVectorError("embedding contains a non-finite value")
        norm = math.sqrt(sum(value * value for value in values))
        if norm <= 1e-12:
            raise EmbeddingProviderInvalidVectorError("embedding is a zero vector")
        if info.normalized and not math.isclose(norm, 1.0, rel_tol=1e-3, abs_tol=1e-3):
            raise EmbeddingProviderInvalidVectorError("embedding is not normalized")
        output.append(values)
    return tuple(output)
