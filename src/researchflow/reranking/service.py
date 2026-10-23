"""Stable candidate reranking that preserves first-stage ranks and score units."""

import math
import time

from researchflow.providers.errors import RerankerProviderInvalidResponseError
from researchflow.reranking.types import (
    RerankCandidate,
    RerankerProvider,
    RerankRequest,
    RerankResult,
)
from researchflow.retrieval.types import RetrievalHit


class RerankService:
    def __init__(self, provider: RerankerProvider, *, timeout_seconds: float) -> None:
        self.provider = provider
        self._timeout_seconds = timeout_seconds

    async def rerank(
        self,
        *,
        query: str,
        candidates: tuple[RetrievalHit, ...],
        top_k: int,
    ) -> RerankResult:
        if not candidates:
            return RerankResult(items=(), model=self.provider.info, duration_ms=0.0)
        if len(candidates) > self.provider.max_batch_size:
            candidates = candidates[: self.provider.max_batch_size]
        request = RerankRequest(
            query=query,
            candidates=tuple(
                RerankCandidate(
                    segment_id=item.segment_id,
                    content=item.content,
                    original_rank=rank,
                )
                for rank, item in enumerate(candidates, start=1)
            ),
        )
        started = time.perf_counter()
        response = await self.provider.score(request, timeout_seconds=self._timeout_seconds)
        duration_ms = (time.perf_counter() - started) * 1000

        expected_ids = {item.segment_id for item in request.candidates}
        scores = {item.segment_id: item.score for item in response.scores}
        if len(scores) != len(response.scores) or set(scores) != expected_ids:
            raise RerankerProviderInvalidResponseError("reranker returned inconsistent IDs")
        if any(not math.isfinite(score) for score in scores.values()):
            raise RerankerProviderInvalidResponseError("reranker returned a non-finite score")

        ranked = sorted(
            (
                item.with_updates(original_rank=rank, rerank_score=scores[item.segment_id])
                for rank, item in enumerate(candidates, start=1)
            ),
            key=lambda item: (-float(item.rerank_score), int(item.original_rank)),
        )
        final = tuple(
            item.with_updates(rerank_rank=rank) for rank, item in enumerate(ranked[:top_k], start=1)
        )
        return RerankResult(items=final, model=response.model, duration_ms=duration_ms)
