"""Provider-neutral value objects for document processing."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class UploadedDocument:
    filename: str
    content_type: str
    raw_content: bytes
    sha256: str
    size: int
    extension: str


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    text: str
    parser_version: str


@dataclass(frozen=True, slots=True)
class TextChunk:
    index: int
    content: str
    char_start: int
    char_end: int
    line_start: int
    line_end: int
    chunker_version: str


@dataclass(frozen=True, slots=True)
class IngestionResult:
    document_id: UUID
    version_id: UUID
    document_key: str
    version_number: int
    sha256: str
    chunk_count: int
    is_duplicate: bool


@dataclass(frozen=True, slots=True)
class VersionSummary:
    id: UUID
    version_number: int
    sha256: str
    original_filename: str
    content_type: str
    file_size: int
    parser_version: str
    created_at: datetime
    chunk_count: int


@dataclass(frozen=True, slots=True)
class DocumentSummary:
    id: UUID
    document_key: str
    title: str
    created_at: datetime
    updated_at: datetime
    current_version: VersionSummary | None


@dataclass(frozen=True, slots=True)
class DocumentPage:
    items: list[DocumentSummary]
    total: int
    offset: int
    limit: int
