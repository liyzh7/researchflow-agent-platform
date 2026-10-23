"""Transactional document ingestion and read use cases."""

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from researchflow.auth.audit import add_audit
from researchflow.auth.context import owner_id
from researchflow.db.database import Database
from researchflow.db.models import Chunk, Document, DocumentVersion, IngestionJob
from researchflow.documents.chunker import CharacterChunker
from researchflow.documents.errors import (
    DatabaseUnavailableError,
    DocumentIngestionError,
    DocumentNotFoundError,
)
from researchflow.documents.parsers import ParserRegistry
from researchflow.documents.types import (
    DocumentPage,
    DocumentSummary,
    IngestionResult,
    UploadedDocument,
    VersionSummary,
)

logger = logging.getLogger("researchflow.documents")


class DocumentService:
    def __init__(
        self,
        database: Database,
        *,
        parser_registry: ParserRegistry,
        chunker: CharacterChunker,
    ) -> None:
        self.database = database
        self.parser_registry = parser_registry
        self.chunker = chunker

    async def ingest(
        self,
        *,
        document_key: str,
        title: str,
        uploaded: UploadedDocument,
    ) -> IngestionResult:
        actor = owner_id()
        parsed = self.parser_registry.parse(uploaded)
        chunks = self.chunker.split(parsed.text)
        if not chunks:
            raise DocumentIngestionError

        try:
            async with self.database.session() as session, session.begin():
                # This database-backed lock also covers the first insert, when no
                # document row exists yet. It is released automatically at commit.
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                    {"key": f"{actor}:{document_key}"},
                )
                document = await session.scalar(
                    select(Document)
                    .where(Document.document_key == document_key, Document.owner_id == actor)
                    .with_for_update()
                )
                if document is None:
                    document = Document(document_key=document_key, title=title, owner_id=actor)
                    session.add(document)
                    await session.flush()
                else:
                    document.title = title
                    document.updated_at = datetime.now(UTC)

                latest = await session.scalar(
                    select(DocumentVersion)
                    .where(DocumentVersion.document_id == document.id)
                    .order_by(DocumentVersion.version_number.desc())
                    .limit(1)
                )
                now = datetime.now(UTC)
                if (
                    latest is not None
                    and latest.sha256 == uploaded.sha256
                    and latest.raw_content == uploaded.raw_content
                ):
                    session.add(
                        IngestionJob(
                            owner_id=actor,
                            document_key=document_key,
                            document_id=document.id,
                            document_version_id=latest.id,
                            status="completed",
                            is_duplicate=True,
                            completed_at=now,
                        )
                    )
                    add_audit(
                        session, "document.upload", "document", document.id, status="duplicate"
                    )
                    return IngestionResult(
                        document_id=document.id,
                        version_id=latest.id,
                        document_key=document_key,
                        version_number=latest.version_number,
                        sha256=latest.sha256,
                        chunk_count=await self._chunk_count(session, latest.id),
                        is_duplicate=True,
                    )

                version = DocumentVersion(
                    document_id=document.id,
                    version_number=1 if latest is None else latest.version_number + 1,
                    sha256=uploaded.sha256,
                    original_filename=uploaded.filename,
                    content_type=uploaded.content_type,
                    file_size=uploaded.size,
                    raw_content=uploaded.raw_content,
                    parsed_text=parsed.text,
                    parser_version=parsed.parser_version,
                )
                session.add(version)
                await session.flush()
                session.add_all(
                    [
                        Chunk(
                            document_version_id=version.id,
                            chunk_index=chunk.index,
                            content=chunk.content,
                            char_start=chunk.char_start,
                            char_end=chunk.char_end,
                            line_start=chunk.line_start,
                            line_end=chunk.line_end,
                            chunker_version=chunk.chunker_version,
                        )
                        for chunk in chunks
                    ]
                )
                session.add(
                    IngestionJob(
                        owner_id=actor,
                        document_key=document_key,
                        document_id=document.id,
                        document_version_id=version.id,
                        status="completed",
                        is_duplicate=False,
                        completed_at=now,
                    )
                )
                await session.flush()
                add_audit(
                    session,
                    "document.upload",
                    "document",
                    document.id,
                    version=version.version_number,
                )
                return IngestionResult(
                    document_id=document.id,
                    version_id=version.id,
                    document_key=document_key,
                    version_number=version.version_number,
                    sha256=version.sha256,
                    chunk_count=len(chunks),
                    is_duplicate=False,
                )
        except OperationalError as exc:
            raise DatabaseUnavailableError from exc
        except SQLAlchemyError as exc:
            await self._record_failed_job(document_key, "transaction_failed")
            raise DocumentIngestionError from exc

    async def list_documents(self, *, offset: int, limit: int) -> DocumentPage:
        actor = owner_id()
        latest_id = (
            select(DocumentVersion.id)
            .where(DocumentVersion.document_id == Document.id)
            .order_by(DocumentVersion.version_number.desc())
            .limit(1)
            .correlate(Document)
            .scalar_subquery()
        )
        chunk_count = (
            select(func.count(Chunk.id))
            .where(Chunk.document_version_id == DocumentVersion.id)
            .correlate(DocumentVersion)
            .scalar_subquery()
        )
        statement = (
            select(Document, DocumentVersion, chunk_count)
            .where(Document.owner_id == actor)
            .outerjoin(DocumentVersion, DocumentVersion.id == latest_id)
            .order_by(Document.created_at.desc(), Document.id)
            .offset(offset)
            .limit(limit)
        )
        try:
            async with self.database.session() as session:
                rows = (await session.execute(statement)).all()
                total = int(
                    await session.scalar(
                        select(func.count(Document.id)).where(Document.owner_id == actor)
                    )
                    or 0
                )
        except OperationalError as exc:
            raise DatabaseUnavailableError from exc

        return DocumentPage(
            items=[self._to_document_summary(*row) for row in rows],
            total=total,
            offset=offset,
            limit=limit,
        )

    async def get_document(self, document_id: UUID) -> DocumentSummary:
        latest_id = (
            select(DocumentVersion.id)
            .where(DocumentVersion.document_id == Document.id)
            .order_by(DocumentVersion.version_number.desc())
            .limit(1)
            .correlate(Document)
            .scalar_subquery()
        )
        chunk_count = (
            select(func.count(Chunk.id))
            .where(Chunk.document_version_id == DocumentVersion.id)
            .correlate(DocumentVersion)
            .scalar_subquery()
        )
        try:
            async with self.database.session() as session:
                row = (
                    await session.execute(
                        select(Document, DocumentVersion, chunk_count)
                        .outerjoin(DocumentVersion, DocumentVersion.id == latest_id)
                        .where(Document.id == document_id, Document.owner_id == owner_id())
                    )
                ).one_or_none()
        except OperationalError as exc:
            raise DatabaseUnavailableError from exc
        if row is None:
            raise DocumentNotFoundError
        return self._to_document_summary(*row)

    async def list_versions(self, document_id: UUID) -> list[VersionSummary]:
        chunk_count = (
            select(func.count(Chunk.id))
            .where(Chunk.document_version_id == DocumentVersion.id)
            .correlate(DocumentVersion)
            .scalar_subquery()
        )
        try:
            async with self.database.session() as session:
                exists = await session.scalar(
                    select(Document.id).where(
                        Document.id == document_id, Document.owner_id == owner_id()
                    )
                )
                if exists is None:
                    raise DocumentNotFoundError
                rows = (
                    await session.execute(
                        select(DocumentVersion, chunk_count)
                        .where(DocumentVersion.document_id == document_id)
                        .order_by(DocumentVersion.version_number.desc())
                    )
                ).all()
        except OperationalError as exc:
            raise DatabaseUnavailableError from exc
        return [self._to_version_summary(version, count) for version, count in rows]

    async def list_chunks(self, document_id: UUID, version_id: UUID) -> list[Chunk]:
        try:
            async with self.database.session() as session:
                matching_version_id = await session.scalar(
                    select(DocumentVersion.id)
                    .join(Document)
                    .where(
                        Document.owner_id == owner_id(),
                        DocumentVersion.document_id == document_id,
                        DocumentVersion.id == version_id,
                    )
                )
                if matching_version_id is None:
                    raise DocumentNotFoundError
                return list(
                    (
                        await session.scalars(
                            select(Chunk)
                            .where(Chunk.document_version_id == matching_version_id)
                            .order_by(Chunk.chunk_index)
                        )
                    ).all()
                )
        except OperationalError as exc:
            raise DatabaseUnavailableError from exc

    async def _record_failed_job(self, document_key: str, error_code: str) -> None:
        try:
            async with self.database.session() as session, session.begin():
                add_audit(
                    session, "document.upload", "document", outcome="failed", reason=error_code
                )
                session.add(
                    IngestionJob(
                        owner_id=owner_id(),
                        document_key=document_key,
                        status="failed",
                        error_code=error_code,
                        is_duplicate=False,
                        completed_at=datetime.now(UTC),
                    )
                )
        except SQLAlchemyError:
            logger.warning("ingestion.failure_audit_unavailable")

    @staticmethod
    async def _chunk_count(session, version_id: UUID) -> int:  # type: ignore[no-untyped-def]
        count = await session.scalar(
            select(func.count(Chunk.id)).where(Chunk.document_version_id == version_id)
        )
        return int(count or 0)

    @classmethod
    def _to_document_summary(
        cls,
        document: Document,
        version: DocumentVersion | None,
        chunk_count: int | None,
    ) -> DocumentSummary:
        return DocumentSummary(
            id=document.id,
            document_key=document.document_key,
            title=document.title,
            created_at=document.created_at,
            updated_at=document.updated_at,
            current_version=(
                cls._to_version_summary(version, chunk_count) if version is not None else None
            ),
        )

    @staticmethod
    def _to_version_summary(version: DocumentVersion, chunk_count: int | None) -> VersionSummary:
        return VersionSummary(
            id=version.id,
            version_number=version.version_number,
            sha256=version.sha256,
            original_filename=version.original_filename,
            content_type=version.content_type,
            file_size=version.file_size,
            parser_version=version.parser_version,
            created_at=version.created_at,
            chunk_count=int(chunk_count or 0),
        )
