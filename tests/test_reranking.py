"""Offline reranker contract, ordering, failure, and concurrency tests."""

import time
from uuid import uuid4

import pytest

from researchflow.core.config import Settings
from researchflow.providers.errors import (
    RerankerProviderInvalidResponseError,
    RerankerProviderTimeoutError,
    RerankerProviderUnavailableError,
)
from researchflow.reranking.cross_encoder import LocalCrossEncoderReranker
from researchflow.reranking.service import RerankService
from researchflow.reranking.types import RerankResponse, RerankScore
from tests.support import DeterministicReranker, make_retrieval_hit


@pytest.mark.anyio
async def test_rerank_orders_bilingual_candidates_and_preserves_first_rank() -> None:
    chinese = make_retrieval_hit("玩具抽屉里有两个空格。")
    english = make_retrieval_hit("The toy drawer has two empty compartments.")
    provider = DeterministicReranker({chinese.segment_id: 1.2, english.segment_id: 2.3})

    result = await RerankService(provider, timeout_seconds=1).rerank(
        query="toy drawer 玩具抽屉",
        candidates=(chinese, english),
        top_k=2,
    )

    assert [item.segment_id for item in result.items] == [english.segment_id, chinese.segment_id]
    assert [item.original_rank for item in result.items] == [2, 1]
    assert [item.rerank_rank for item in result.items] == [1, 2]
    assert [item.rerank_score for item in result.items] == [2.3, 1.2]


@pytest.mark.anyio
async def test_equal_scores_keep_original_order() -> None:
    hits = tuple(make_retrieval_hit(segment_id=uuid4()) for _ in range(3))
    provider = DeterministicReranker({item.segment_id: 0.5 for item in hits})
    result = await RerankService(provider, timeout_seconds=1).rerank(
        query="evidence", candidates=hits, top_k=3
    )
    assert [item.segment_id for item in result.items] == [item.segment_id for item in hits]


@pytest.mark.anyio
async def test_service_enforces_provider_batch_limit() -> None:
    hits = tuple(make_retrieval_hit() for _ in range(4))
    provider = DeterministicReranker(max_batch_size=2)
    result = await RerankService(provider, timeout_seconds=1).rerank(
        query="evidence", candidates=hits, top_k=4
    )
    assert len(result.items) == 2
    assert provider.last_request is not None
    assert len(provider.last_request.candidates) == 2


@pytest.mark.anyio
async def test_inconsistent_provider_ids_are_rejected() -> None:
    hit = make_retrieval_hit()

    class InvalidProvider(DeterministicReranker):
        async def score(self, request, *, timeout_seconds):  # type: ignore[no-untyped-def]
            return RerankResponse(
                scores=(RerankScore(segment_id=uuid4(), score=1.0),), model=self.info
            )

    with pytest.raises(RerankerProviderInvalidResponseError):
        await RerankService(InvalidProvider(), timeout_seconds=1).rerank(
            query="evidence", candidates=(hit,), top_k=1
        )


@pytest.mark.anyio
async def test_cross_encoder_missing_pinned_snapshot_is_unavailable(tmp_path) -> None:
    settings = Settings(
        environment="test",
        database_password="test",
        reranker_enabled=True,
        reranker_cache_dir=tmp_path,
        _env_file=None,
    )
    provider = LocalCrossEncoderReranker(settings)
    with pytest.raises(RerankerProviderUnavailableError, match="not cached"):
        await provider._ensure_model(1)


@pytest.mark.anyio
async def test_cross_encoder_timeout_is_classified(settings: Settings) -> None:
    provider = LocalCrossEncoderReranker(settings)
    provider._model = object()
    hit = make_retrieval_hit()

    def slow_score(*args):  # type: ignore[no-untyped-def]
        time.sleep(0.05)
        return (1.0,)

    provider._score_sync = slow_score  # type: ignore[method-assign]
    request = provider_request(hit)
    with pytest.raises(RerankerProviderTimeoutError):
        await provider.score(request, timeout_seconds=0.001)


def provider_request(hit):  # type: ignore[no-untyped-def]
    from researchflow.reranking.types import RerankCandidate, RerankRequest

    return RerankRequest(
        query="evidence",
        candidates=(
            RerankCandidate(segment_id=hit.segment_id, content=hit.content, original_rank=1),
        ),
    )
