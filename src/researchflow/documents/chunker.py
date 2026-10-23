"""Deterministic character-window chunking with exact normalized offsets."""

from bisect import bisect_left

from researchflow.documents.types import TextChunk


class CharacterChunker:
    version = "character-window-v1"

    def __init__(self, *, chunk_size: int, overlap: int) -> None:
        if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
            raise ValueError("chunk_size must be positive and overlap must be smaller")
        self.chunk_size = chunk_size
        self.overlap = overlap

    def split(self, text: str) -> list[TextChunk]:
        if not text:
            return []

        newline_offsets = [index for index, char in enumerate(text) if char == "\n"]
        chunks: list[TextChunk] = []
        start = 0
        while start < len(text):
            end = min(start + self.chunk_size, len(text))
            final_character = end - 1
            chunks.append(
                TextChunk(
                    index=len(chunks),
                    content=text[start:end],
                    char_start=start,
                    char_end=end,
                    line_start=bisect_left(newline_offsets, start) + 1,
                    line_end=bisect_left(newline_offsets, final_character) + 1,
                    chunker_version=self.version,
                )
            )
            if end == len(text):
                break
            start = end - self.overlap
        return chunks
