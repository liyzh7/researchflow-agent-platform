"""Opt-in smoke tests against the pinned real E5 snapshot."""

import math
import os

import pytest

from researchflow.core.config import Settings
from researchflow.providers.embedding import EmbeddingRequest
from researchflow.providers.local_embedding import LocalEmbeddingProvider

pytestmark = pytest.mark.real_embedding


@pytest.fixture(scope="module")
def real_provider() -> LocalEmbeddingProvider:
    if os.environ.get("RESEARCHFLOW_RUN_REAL_EMBEDDING") != "1":
        pytest.skip("set RESEARCHFLOW_RUN_REAL_EMBEDDING=1 to load the pinned model")
    settings = Settings(
        _env_file=None, environment="test", database_password="synthetic-model-smoke"
    )
    return LocalEmbeddingProvider(settings)


@pytest.mark.anyio
async def test_real_embedding_is_384d_normalized_and_deterministic(
    real_provider: LocalEmbeddingProvider,
) -> None:
    request = EmbeddingRequest(texts=("玩具抽屉标签查找", "invented drawer label lookup"))
    first = await real_provider.embed_queries(request, timeout_seconds=120)
    second = await real_provider.embed_queries(request, timeout_seconds=120)

    assert first.model.revision == "614241f622f53c4eeff9890bdc4f31cfecc418b3"
    assert first.vectors == second.vectors
    assert all(len(vector) == 384 for vector in first.vectors)
    assert all(
        math.isclose(math.sqrt(sum(value * value for value in vector)), 1.0, abs_tol=1e-5)
        for vector in first.vectors
    )


@pytest.mark.anyio
async def test_real_tokenizer_reports_exact_offsets(real_provider: LocalEmbeddingProvider) -> None:
    text = "Copper 方格 silver: offset specimen."
    tokenization = await real_provider.tokenize_document(text, timeout_seconds=120)

    assert tokenization.offsets
    assert all(0 <= start < end <= len(text) for start, end in tokenization.offsets)
    assert tokenization.prefixed_token_count <= tokenization.max_input_tokens
