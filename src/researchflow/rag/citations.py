"""Server-side source re-fetch and legal citation validation."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from researchflow.auth.context import owner_id
from researchflow.db.database import Database
from researchflow.observability.tracing import traced
from researchflow.rag.errors import (
    CitationRepositoryUnavailableError,
    CitationValidationError,
)
from researchflow.rag.types import GeneratedCitation, PromptEvidence, VerifiedCitation

_SOURCE_SQL = """
WITH current_versions AS (
    SELECT DISTINCT ON (document_id) id, document_id, version_number
    FROM document_versions
    ORDER BY document_id, version_number DESC
)
SELECT
    d.id AS document_id,
    d.document_key,
    dv.id AS version_id,
    cv.version_number,
    c.id AS chunk_id,
    c.chunk_index,
    s.id AS segment_id,
    s.segment_index,
    s.content,
    s.char_start,
    s.char_end,
    s.line_start,
    s.line_end,
    dv.parsed_text
FROM index_segments AS s
JOIN chunks AS c
  ON c.id = s.chunk_id
 AND c.document_version_id = s.document_version_id
JOIN document_versions AS dv ON dv.id = s.document_version_id
JOIN current_versions AS cv ON cv.id = dv.id
JOIN documents AS d
  ON d.id = dv.document_id
 AND d.id = cv.document_id
WHERE s.id = ANY(CAST(:segment_ids AS uuid[]))
  AND d.owner_id = CAST(:owner_id AS uuid)
"""


@dataclass(frozen=True, slots=True)
class EvidenceSourceRecord:
    document_id: UUID
    document_key: str
    version_id: UUID
    version_number: int
    chunk_id: UUID
    chunk_index: int
    segment_id: UUID
    segment_index: int
    content: str
    char_start: int
    char_end: int
    line_start: int
    line_end: int
    parsed_text: str


class EvidenceSourceRepository(Protocol):
    async def fetch_current(
        self, segment_ids: tuple[UUID, ...]
    ) -> dict[UUID, EvidenceSourceRecord]: ...


class PostgresEvidenceSourceRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def fetch_current(
        self, segment_ids: tuple[UUID, ...]
    ) -> dict[UUID, EvidenceSourceRecord]:
        if not segment_ids:
            return {}
        try:
            async with self._database.session() as session:
                rows = (
                    (
                        await session.execute(
                            text(_SOURCE_SQL),
                            {
                                "segment_ids": [str(segment_id) for segment_id in segment_ids],
                                "owner_id": str(owner_id()),
                            },
                        )
                    )
                    .mappings()
                    .all()
                )
        except SQLAlchemyError as exc:
            raise CitationRepositoryUnavailableError from exc
        return {row["segment_id"]: EvidenceSourceRecord(**dict(row)) for row in rows}


class CitationService:
    def __init__(self, repository: EvidenceSourceRepository) -> None:
        self._repository = repository

    @traced("citation.validation")
    async def verify(
        self,
        generated: tuple[GeneratedCitation, ...],
        allowed: tuple[PromptEvidence, ...],
    ) -> tuple[VerifiedCitation, ...]:
        allowed_by_id = {item.evidence_id: item for item in allowed}
        requested_ids = [item.evidence_id for item in generated]
        if len(requested_ids) != len(set(requested_ids)):
            raise CitationValidationError("duplicate evidence citation")
        if any(evidence_id not in allowed_by_id for evidence_id in requested_ids):
            raise CitationValidationError("citation references an unprovided evidence ID")

        selected = tuple(allowed_by_id[evidence_id] for evidence_id in requested_ids)
        records = await self._repository.fetch_current(
            tuple(item.hit.segment_id for item in selected)
        )
        verified: list[VerifiedCitation] = []
        for item in selected:
            hit = item.hit
            source = records.get(hit.segment_id)
            if source is None or not _matches_hit(source, hit):
                raise CitationValidationError("citation source is stale or inconsistent")
            if source.parsed_text[source.char_start : source.char_end] != source.content:
                raise CitationValidationError("citation source range does not match stored text")
            verified.append(
                VerifiedCitation(
                    evidence_id=item.evidence_id,
                    document_id=source.document_id,
                    document_key=source.document_key,
                    version_id=source.version_id,
                    version_number=source.version_number,
                    chunk_id=source.chunk_id,
                    chunk_index=source.chunk_index,
                    segment_id=source.segment_id,
                    segment_index=source.segment_index,
                    char_start=source.char_start,
                    char_end=source.char_end,
                    line_start=source.line_start,
                    line_end=source.line_end,
                    excerpt=source.content,
                )
            )
        return tuple(verified)


def _matches_hit(source: EvidenceSourceRecord, hit: object) -> bool:
    fields = (
        "document_id",
        "document_key",
        "version_id",
        "version_number",
        "chunk_id",
        "chunk_index",
        "segment_id",
        "segment_index",
        "content",
        "char_start",
        "char_end",
        "line_start",
        "line_end",
    )
    return all(getattr(source, field) == getattr(hit, field) for field in fields)
