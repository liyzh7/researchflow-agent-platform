"""Opt-in real inference for the explicitly approved pinned CrossEncoder."""

import logging
import math
import os

import pytest

from researchflow.core.config import Settings
from researchflow.reranking.cross_encoder import LocalCrossEncoderReranker
from researchflow.reranking.types import RerankCandidate, RerankRequest

pytestmark = [
    pytest.mark.real_reranker,
    pytest.mark.skipif(
        os.getenv("RESEARCHFLOW_RUN_REAL_RERANKER") != "1",
        reason="set RESEARCHFLOW_RUN_REAL_RERANKER=1 after model download approval",
    ),
]


@pytest.mark.anyio
async def test_pinned_real_reranker_scores_bilingual_pairs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    settings = Settings(
        _env_file=None,
        environment="test",
        database_password="synthetic-model-smoke",
        reranker_enabled=True,
        reranker_local_files_only=True,
    )
    provider = LocalCrossEncoderReranker(settings)
    relevant = RerankCandidate(
        segment_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        content="A fictional shelf ledger stores card labels in a database.",
        original_rank=1,
    )
    irrelevant = RerankCandidate(
        segment_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        content="虚构的橙色风筝停在窗边。",
        original_rank=2,
    )
    result = await provider.score(
        RerankRequest(
            query="Where does the fictional shelf ledger store card labels?",
            candidates=(relevant, irrelevant),
        ),
        timeout_seconds=settings.reranker_timeout_seconds,
    )
    scores = {item.segment_id: item.score for item in result.scores}
    assert all(math.isfinite(score) for score in scores.values())
    assert scores[relevant.segment_id] > scores[irrelevant.segment_id]
    assert "deprecated" not in caplog.text.casefold()
