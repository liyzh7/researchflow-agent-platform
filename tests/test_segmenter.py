"""Exact-offset and no-silent-truncation tests for token-aware segmentation."""

from uuid import uuid4

import pytest

from researchflow.db.models import Chunk
from researchflow.indexing.errors import IndexingSourceMismatchError
from researchflow.indexing.segmenter import TokenAwareSegmenter
from tests.support import DeterministicEmbeddingProvider


def make_chunk(text: str, *, start: int = 0, line_start: int = 1) -> Chunk:
    return Chunk(
        id=uuid4(),
        document_version_id=uuid4(),
        chunk_index=0,
        content=text,
        char_start=start,
        char_end=start + len(text),
        line_start=line_start,
        line_end=line_start + text.count("\n"),
        chunker_version="test",
    )


@pytest.mark.anyio
async def test_short_chunk_stays_whole() -> None:
    provider = DeterministicEmbeddingProvider(max_input_tokens=12)
    segmenter = TokenAwareSegmenter(
        provider, max_content_tokens=8, overlap_tokens=2, timeout_seconds=1
    )
    text = "copper 方格 zinc"
    segments = await segmenter.split_chunk(make_chunk(text), text)

    assert len(segments) == 1
    assert segments[0].content == text
    assert segments[0].char_start == 0
    assert segments[0].char_end == len(text)
    assert segments[0].token_count <= 12


@pytest.mark.anyio
async def test_long_chunk_uses_overlapping_token_windows_with_exact_offsets() -> None:
    provider = DeterministicEmbeddingProvider(max_input_tokens=8)
    segmenter = TokenAwareSegmenter(
        provider, max_content_tokens=6, overlap_tokens=2, timeout_seconds=1
    )
    text = "tin copper lead\nnickel zinc silver gold iron neon argon"
    source = "header\n" + text
    chunk = make_chunk(text, start=len("header\n"), line_start=2)
    segments = await segmenter.split_chunk(chunk, source)

    assert len(segments) >= 2
    for segment in segments:
        assert source[segment.char_start : segment.char_end] == segment.content
        assert segment.token_count <= provider.info.max_input_tokens
    assert "zinc" in segments[0].content
    assert "zinc" in segments[1].content
    assert segments[1].line_start >= 2


@pytest.mark.anyio
async def test_source_mismatch_is_not_silently_indexed() -> None:
    segmenter = TokenAwareSegmenter(
        DeterministicEmbeddingProvider(),
        max_content_tokens=100,
        overlap_tokens=10,
        timeout_seconds=1,
    )
    with pytest.raises(IndexingSourceMismatchError):
        await segmenter.split_chunk(make_chunk("different"), "source")
