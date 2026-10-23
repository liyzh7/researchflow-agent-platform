"""Deterministic tokenizer-aware secondary splitting with exact source offsets."""

from bisect import bisect_left

from researchflow.db.models import Chunk
from researchflow.indexing.errors import IndexingSourceMismatchError
from researchflow.indexing.types import PreparedSegment
from researchflow.providers.embedding import EmbeddingProvider
from researchflow.providers.errors import EmbeddingProviderInvalidInputError


class TokenAwareSegmenter:
    def __init__(
        self,
        provider: EmbeddingProvider,
        *,
        max_content_tokens: int,
        overlap_tokens: int,
        timeout_seconds: float,
    ) -> None:
        if max_content_tokens <= 0 or overlap_tokens < 0 or overlap_tokens >= max_content_tokens:
            raise ValueError("token segment size must be positive and larger than overlap")
        self.provider = provider
        self.max_content_tokens = max_content_tokens
        self.overlap_tokens = overlap_tokens
        self.timeout_seconds = timeout_seconds

    async def split_chunk(self, chunk: Chunk, parsed_text: str) -> list[PreparedSegment]:
        if parsed_text[chunk.char_start : chunk.char_end] != chunk.content:
            raise IndexingSourceMismatchError("chunk content does not match normalized source")

        tokenization = await self.provider.tokenize_document(
            chunk.content, timeout_seconds=self.timeout_seconds
        )
        if not tokenization.offsets:
            raise EmbeddingProviderInvalidInputError("chunk has no tokenizable content")

        if tokenization.prefixed_token_count <= tokenization.max_input_tokens:
            token_count = tokenization.prefixed_token_count
            return [self._make_segment(chunk, parsed_text, 0, len(chunk.content), 0, token_count)]

        windows: list[tuple[int, int]] = []
        offsets = tokenization.offsets
        token_start = 0
        while token_start < len(offsets):
            token_end = min(token_start + self.max_content_tokens, len(offsets))
            char_start = 0 if token_start == 0 else offsets[token_start][0]
            char_end = len(chunk.content) if token_end == len(offsets) else offsets[token_end][0]
            if char_end <= char_start:
                raise IndexingSourceMismatchError("tokenizer produced an invalid source range")
            windows.append((char_start, char_end))
            if token_end == len(offsets):
                break
            token_start = token_end - self.overlap_tokens

        contents = tuple(chunk.content[start:end] for start, end in windows)
        counts: list[int] = []
        for batch_start in range(0, len(contents), self.provider.max_batch_size):
            batch = contents[batch_start : batch_start + self.provider.max_batch_size]
            counts.extend(
                await self.provider.count_document_tokens(
                    batch, timeout_seconds=self.timeout_seconds
                )
            )
        if any(count > tokenization.max_input_tokens for count in counts):
            raise EmbeddingProviderInvalidInputError(
                "configured segment token budget does not prevent model truncation"
            )

        return [
            self._make_segment(chunk, parsed_text, start, end, index, counts[index])
            for index, (start, end) in enumerate(windows)
        ]

    @staticmethod
    def _make_segment(
        chunk: Chunk,
        parsed_text: str,
        local_start: int,
        local_end: int,
        index: int,
        token_count: int,
    ) -> PreparedSegment:
        global_start = chunk.char_start + local_start
        global_end = chunk.char_start + local_end
        content = chunk.content[local_start:local_end]
        if parsed_text[global_start:global_end] != content:
            raise IndexingSourceMismatchError("segment content does not match normalized source")

        newline_offsets = [position for position, char in enumerate(chunk.content) if char == "\n"]
        final_character = local_end - 1
        return PreparedSegment(
            chunk_id=chunk.id,
            document_version_id=chunk.document_version_id,
            segment_index=index,
            content=content,
            char_start=global_start,
            char_end=global_end,
            line_start=chunk.line_start + bisect_left(newline_offsets, local_start),
            line_end=chunk.line_start + bisect_left(newline_offsets, final_character),
            token_count=token_count,
        )
