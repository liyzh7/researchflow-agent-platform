"""Immutable value objects used by the indexing workflow."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID


@dataclass(frozen=True, slots=True)
class PreparedSegment:
    chunk_id: UUID
    document_version_id: UUID
    segment_index: int
    content: str
    char_start: int
    char_end: int
    line_start: int
    line_end: int
    token_count: int


@dataclass(frozen=True, slots=True)
class IndexingResult:
    document_version_id: UUID
    model_config_id: UUID
    status: Literal["completed", "already_completed", "in_progress"]
    segment_count: int
    attempt_count: int
