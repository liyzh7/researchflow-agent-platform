"""Embedding contract tests that do not load a real model."""

from pathlib import Path

import pytest

from researchflow.core.config import Settings
from researchflow.providers.embedding import EmbeddingRequest, validate_embedding_vectors
from researchflow.providers.errors import (
    EmbeddingProviderInvalidInputError,
    EmbeddingProviderInvalidVectorError,
)
from researchflow.providers.local_embedding import LocalEmbeddingProvider


@pytest.fixture
def provider(tmp_path: Path) -> LocalEmbeddingProvider:
    settings = Settings(
        environment="test",
        database_password="unit-test-password",
        embedding_cache_dir=tmp_path,
        _env_file=None,
    )
    return LocalEmbeddingProvider(settings)


def test_e5_prefixes_are_added_once(provider: LocalEmbeddingProvider) -> None:
    assert (
        provider._add_prefix("which toy row has a disk?", "query")
        == "query: which toy row has a disk?"
    )
    assert (
        provider._add_prefix("query: which toy row has a disk?", "query")
        == "query: which toy row has a disk?"
    )
    assert (
        provider._add_prefix("a toy row contains a disk", "document")
        == "passage: a toy row contains a disk"
    )


def test_wrong_e5_prefix_is_rejected(provider: LocalEmbeddingProvider) -> None:
    with pytest.raises(EmbeddingProviderInvalidInputError):
        provider._add_prefix("passage: a toy row is stored here", "query")


def test_blank_and_oversized_batches_are_rejected(provider: LocalEmbeddingProvider) -> None:
    with pytest.raises(EmbeddingProviderInvalidInputError):
        provider._validate_text_batch((" ",))
    with pytest.raises(EmbeddingProviderInvalidInputError):
        provider._validate_text_batch(tuple("x" for _ in range(provider.max_batch_size + 1)))


@pytest.mark.parametrize(
    "vectors",
    [
        ((0.0,) * 384,),
        ((1.0,) * 383,),
        ((float("nan"),) + (0.0,) * 383,),
        ((1.0,) + (0.0,) * 383, (1.0,) + (0.0,) * 383),
    ],
)
def test_invalid_provider_vectors_are_rejected(
    provider: LocalEmbeddingProvider, vectors: tuple[tuple[float, ...], ...]
) -> None:
    with pytest.raises(EmbeddingProviderInvalidVectorError):
        validate_embedding_vectors(vectors, provider.info, expected_count=1)


@pytest.mark.anyio
async def test_missing_pinned_snapshot_fails_without_network(
    provider: LocalEmbeddingProvider,
) -> None:
    from researchflow.providers.errors import EmbeddingProviderUnavailableError

    with pytest.raises(EmbeddingProviderUnavailableError):
        await provider.embed_queries(
            EmbeddingRequest(texts=("offline",)),
            timeout_seconds=5,
        )
