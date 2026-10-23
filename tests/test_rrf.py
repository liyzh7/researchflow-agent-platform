"""Reciprocal-rank fusion behavior."""

from uuid import uuid4

import pytest

from researchflow.retrieval.rrf import reciprocal_rank_fusion
from researchflow.retrieval.types import RetrievalHit


def hit(segment_id=None, **scores) -> RetrievalHit:  # type: ignore[no-untyped-def]
    return RetrievalHit(
        document_id=uuid4(),
        document_key="paper",
        title="Paper",
        version_id=uuid4(),
        version_number=1,
        chunk_id=uuid4(),
        chunk_index=0,
        segment_id=segment_id or uuid4(),
        segment_index=0,
        content="evidence",
        char_start=0,
        char_end=8,
        line_start=1,
        line_end=1,
        retrieval_method="vector",
        **scores,
    )


def test_rrf_rewards_items_present_in_both_rankings() -> None:
    shared = uuid4()
    vector_only = hit(vector_distance=0.1)
    keyword_only = hit(keyword_score=1.0)
    shared_vector = hit(shared, vector_distance=0.2)
    shared_keyword = hit(shared, keyword_score=0.8)

    fused = reciprocal_rank_fusion(
        [vector_only, shared_vector], [keyword_only, shared_keyword], rrf_k=60, top_k=3
    )

    assert fused[0].segment_id == shared
    assert fused[0].retrieval_method == "hybrid"
    assert fused[0].vector_rank == 2
    assert fused[0].keyword_rank == 2
    assert fused[0].rrf_score == pytest.approx(2 / 62)


def test_rrf_validates_parameters() -> None:
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([], [], rrf_k=0, top_k=1)


def test_rrf_order_is_stable_for_ties() -> None:
    first = hit()
    second = hit()
    expected = reciprocal_rank_fusion([first, second], [], rrf_k=60, top_k=2)

    assert reciprocal_rank_fusion([first, second], [], rrf_k=60, top_k=2) == expected
