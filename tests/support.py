"""Deterministic, network-free test doubles shared by stage 3 tests."""

import hashlib
import math
import re
from dataclasses import replace
from uuid import UUID, uuid4

from researchflow.providers.embedding import (
    DocumentTokenization,
    EmbeddingModelInfo,
    EmbeddingRequest,
    EmbeddingResponse,
)
from researchflow.providers.errors import EmbeddingProviderTimeoutError
from researchflow.providers.llm import LLMRequest, LLMResponse
from researchflow.rag.citations import EvidenceSourceRecord
from researchflow.reranking.types import (
    RerankerInfo,
    RerankRequest,
    RerankResponse,
    RerankScore,
)
from researchflow.retrieval.types import RetrievalHit, RetrievalResult

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+|[\u3400-\u9fff]|[^\w\s]", re.UNICODE)


class DeterministicEmbeddingProvider:
    name = "deterministic-test"
    max_batch_size = 8

    def __init__(self, *, max_input_tokens: int = 512, fail_count: int = 0) -> None:
        self.info = EmbeddingModelInfo(
            provider=self.name,
            model_name="test-hash-embedding",
            revision="test-only-v1",
            dimension=384,
            normalized=True,
            query_prefix="query: ",
            passage_prefix="passage: ",
            max_input_tokens=max_input_tokens,
        )
        self.fail_count = fail_count

    async def embed_queries(
        self, request: EmbeddingRequest, *, timeout_seconds: float
    ) -> EmbeddingResponse:
        return await self._embed(request, purpose="query")

    async def embed_documents(
        self, request: EmbeddingRequest, *, timeout_seconds: float
    ) -> EmbeddingResponse:
        return await self._embed(request, purpose="document")

    async def tokenize_document(self, text: str, *, timeout_seconds: float) -> DocumentTokenization:
        offsets = tuple(match.span() for match in _TOKEN_PATTERN.finditer(text))
        return DocumentTokenization(
            offsets=offsets,
            prefixed_token_count=len(offsets) + 2,
            max_input_tokens=self.info.max_input_tokens,
        )

    async def count_document_tokens(
        self, texts: tuple[str, ...], *, timeout_seconds: float
    ) -> tuple[int, ...]:
        return tuple(len(tuple(_TOKEN_PATTERN.finditer(text))) + 2 for text in texts)

    async def _embed(self, request: EmbeddingRequest, *, purpose: str) -> EmbeddingResponse:
        if self.fail_count > 0:
            self.fail_count -= 1
            raise EmbeddingProviderTimeoutError
        vectors = tuple(self._vector(text) for text in request.texts)
        return EmbeddingResponse(vectors=vectors, model=self.info, purpose=purpose)

    @staticmethod
    def _vector(text: str) -> tuple[float, ...]:
        values = [0.0] * 384
        tokens = [match.group(0).casefold() for match in _TOKEN_PATTERN.finditer(text)]
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            position = int.from_bytes(digest[:2], "big") % len(values)
            sign = 1.0 if digest[2] & 1 else -1.0
            values[position] += sign
        if not tokens:
            values[0] = 1.0
        norm = math.sqrt(sum(value * value for value in values))
        return tuple(value / norm for value in values)


class InvalidVectorEmbeddingProvider(DeterministicEmbeddingProvider):
    async def embed_documents(
        self, request: EmbeddingRequest, *, timeout_seconds: float
    ) -> EmbeddingResponse:
        return EmbeddingResponse(
            vectors=((0.0,) * 383,) * len(request.texts),
            model=self.info,
            purpose="document",
        )


def make_retrieval_hit(
    content: str = "A toy shelf ledger lists two empty slots.",
    *,
    document_key: str = "evidence-doc",
    rerank_score: float | None = None,
    segment_id: UUID | None = None,
    **changes: object,
) -> RetrievalHit:
    hit = RetrievalHit(
        document_id=uuid4(),
        document_key=document_key,
        title="Invented toy-shelf ledger",
        version_id=uuid4(),
        version_number=1,
        chunk_id=uuid4(),
        chunk_index=0,
        segment_id=segment_id or uuid4(),
        segment_index=0,
        content=content,
        char_start=0,
        char_end=len(content),
        line_start=1,
        line_end=1,
        retrieval_method="hybrid",
        rrf_score=0.02,
        rerank_score=rerank_score,
    )
    return replace(hit, **changes)


class DeterministicReranker:
    name = "deterministic-reranker"

    def __init__(
        self,
        scores: dict[UUID, float] | None = None,
        *,
        max_batch_size: int = 12,
    ) -> None:
        self.info = RerankerInfo(
            provider=self.name,
            model_name="test-reranker",
            revision="test-only-v1",
            max_input_tokens=512,
        )
        self.max_batch_size = max_batch_size
        self.scores = scores or {}
        self.last_request: RerankRequest | None = None

    async def score(self, request: RerankRequest, *, timeout_seconds: float) -> RerankResponse:
        self.last_request = request
        return RerankResponse(
            scores=tuple(
                RerankScore(
                    segment_id=item.segment_id,
                    score=self.scores.get(item.segment_id, float(-item.original_rank)),
                )
                for item in request.candidates
            ),
            model=self.info,
        )


class StubRetrievalService:
    def __init__(self, items: tuple[RetrievalHit, ...]) -> None:
        self.items = items
        self.provider = DeterministicEmbeddingProvider()

    async def search(self, **arguments) -> RetrievalResult:  # type: ignore[no-untyped-def]
        return RetrievalResult(
            status="ok" if self.items else "no_indexed_documents",
            mode=arguments["mode"],
            items=self.items,
            model=self.provider.info,
            rrf_k=60 if arguments["mode"] == "hybrid" else None,
        )


class InMemoryEvidenceSourceRepository:
    def __init__(self, hits: tuple[RetrievalHit, ...]) -> None:
        self.records = {
            hit.segment_id: EvidenceSourceRecord(
                document_id=hit.document_id,
                document_key=hit.document_key,
                version_id=hit.version_id,
                version_number=hit.version_number,
                chunk_id=hit.chunk_id,
                chunk_index=hit.chunk_index,
                segment_id=hit.segment_id,
                segment_index=hit.segment_index,
                content=hit.content,
                char_start=hit.char_start,
                char_end=hit.char_end,
                line_start=hit.line_start,
                line_end=hit.line_end,
                parsed_text=hit.content,
            )
            for hit in hits
        }

    async def fetch_current(
        self, segment_ids: tuple[UUID, ...]
    ) -> dict[UUID, EvidenceSourceRecord]:
        return {
            segment_id: self.records[segment_id]
            for segment_id in segment_ids
            if segment_id in self.records
        }


class CountingStructuredProvider:
    name = "structured-test"

    def __init__(self, *, content: str | None = None, is_mock: bool = True) -> None:
        self.content = content
        self.is_mock = is_mock
        self.call_count = 0
        self.last_request: LLMRequest | None = None

    async def generate(self, request: LLMRequest, *, timeout_seconds: float) -> LLMResponse:
        self.call_count += 1
        self.last_request = request
        content = self.content or (
            '{"answer":"[MOCK] The invented shelf has two empty slots.",'
            '"citations":[{"evidence_id":"E1"}]}'
        )
        return LLMResponse(
            content=content,
            provider=self.name,
            model="structured-test-v1",
            is_mock=self.is_mock,
        )
