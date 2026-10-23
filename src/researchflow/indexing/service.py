"""Retryable, idempotent document-version embedding index workflow."""

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from researchflow.auth.audit import add_audit
from researchflow.auth.context import owner_id
from researchflow.core.config import Settings
from researchflow.db.database import Database
from researchflow.db.models import (
    Chunk,
    Document,
    DocumentVersion,
    EmbeddingModelConfig,
    IndexingJob,
    IndexSegment,
)
from researchflow.indexing.errors import (
    IndexingAttemptsExhaustedError,
    IndexingError,
    IndexingSourceNotFoundError,
)
from researchflow.indexing.segmenter import TokenAwareSegmenter
from researchflow.indexing.types import IndexingResult, PreparedSegment
from researchflow.providers.embedding import (
    EmbeddingProvider,
    EmbeddingRequest,
    embedding_config_key,
    validate_embedding_vectors,
)

logger = logging.getLogger("researchflow.indexing")


class IndexingService:
    def __init__(
        self,
        database: Database,
        provider: EmbeddingProvider,
        settings: Settings,
    ) -> None:
        self.database = database
        self.provider = provider
        self.settings = settings
        self.segmenter = TokenAwareSegmenter(
            provider,
            max_content_tokens=settings.embedding_segment_tokens,
            overlap_tokens=settings.embedding_segment_overlap_tokens,
            timeout_seconds=settings.embedding_timeout_seconds,
        )

    async def index_version(self, document_version_id: UUID) -> IndexingResult:
        await self._load_source(document_version_id)
        model_config = await self.ensure_model_config()
        preparation = await self._prepare_job(document_version_id, model_config.id)
        if preparation.status != "completed":
            return preparation

        try:
            parsed_text, chunks = await self._load_source(document_version_id)
            segments: list[PreparedSegment] = []
            for chunk in chunks:
                segments.extend(await self.segmenter.split_chunk(chunk, parsed_text))
            if not segments:
                raise IndexingError("document version produced no index segments")

            vectors: list[tuple[float, ...]] = []
            for batch_start in range(0, len(segments), self.provider.max_batch_size):
                batch = segments[batch_start : batch_start + self.provider.max_batch_size]
                response = await self.provider.embed_documents(
                    EmbeddingRequest(texts=tuple(segment.content for segment in batch)),
                    timeout_seconds=self.settings.embedding_timeout_seconds,
                )
                if response.model != self.provider.info or len(response.vectors) != len(batch):
                    raise IndexingError("embedding provider returned inconsistent metadata")
                vectors.extend(
                    validate_embedding_vectors(
                        response.vectors, self.provider.info, expected_count=len(batch)
                    )
                )

            return await self._commit_segments(
                document_version_id,
                model_config.id,
                segments,
                vectors,
                preparation.attempt_count,
            )
        except Exception as exc:
            await self._mark_failed(document_version_id, model_config.id, self._error_code(exc))
            raise

    async def status(self, document_version_id: UUID):
        await self._load_source(document_version_id)
        async with self.database.session() as session:
            job = await session.scalar(
                select(IndexingJob)
                .join(EmbeddingModelConfig)
                .where(
                    IndexingJob.document_version_id == document_version_id,
                    EmbeddingModelConfig.config_key == embedding_config_key(self.provider.info),
                )
            )
            return {
                "status": "not_indexed" if job is None else job.status,
                "segment_count": 0 if job is None else job.segment_count,
                "error_code": None if job is None else job.error_code,
                "updated_at": None if job is None else job.updated_at,
            }

    async def index_targets(
        self,
        *,
        document_key: str | None = None,
        include_historical: bool = False,
    ) -> list[IndexingResult]:
        version_ids = await self._target_version_ids(
            document_key=document_key, include_historical=include_historical
        )
        return [await self.index_version(version_id) for version_id in version_ids]

    async def ensure_model_config(self) -> EmbeddingModelConfig:
        info = self.provider.info
        if info.dimension != self.settings.embedding_dimension:
            raise IndexingError("embedding provider dimension does not match the vector schema")
        config_key = embedding_config_key(info)
        values = {
            "config_key": config_key,
            "provider": info.provider,
            "model_name": info.model_name,
            "revision": info.revision,
            "dimension": info.dimension,
            "normalized": info.normalized,
            "query_prefix": info.query_prefix,
            "passage_prefix": info.passage_prefix,
            "max_input_tokens": info.max_input_tokens,
        }
        async with self.database.session() as session, session.begin():
            statement = (
                insert(EmbeddingModelConfig)
                .values(**values)
                .on_conflict_do_nothing(index_elements=[EmbeddingModelConfig.config_key])
                .returning(EmbeddingModelConfig.id)
            )
            inserted_id = await session.scalar(statement)
            config_id = inserted_id or await session.scalar(
                select(EmbeddingModelConfig.id).where(EmbeddingModelConfig.config_key == config_key)
            )
        if config_id is None:
            raise IndexingError("embedding model configuration could not be persisted")
        async with self.database.session() as session:
            model_config = await session.get(EmbeddingModelConfig, config_id)
            if model_config is None:
                raise IndexingError("embedding model configuration disappeared")
            return model_config

    async def _prepare_job(self, version_id: UUID, model_config_id: UUID) -> IndexingResult:
        now = datetime.now(UTC)
        stale_before = now - timedelta(seconds=self.settings.indexing_stale_after_seconds)
        async with self.database.session() as session, session.begin():
            await self._advisory_lock(session, version_id, model_config_id)
            exists = await session.scalar(
                select(DocumentVersion.id)
                .join(Document)
                .where(DocumentVersion.id == version_id, Document.owner_id == owner_id())
            )
            if exists is None:
                raise IndexingSourceNotFoundError("document version does not exist")
            job = await session.scalar(
                select(IndexingJob)
                .where(
                    IndexingJob.document_version_id == version_id,
                    IndexingJob.model_config_id == model_config_id,
                )
                .with_for_update()
            )
            if job is not None and job.status == "completed":
                count = int(
                    await session.scalar(
                        select(func.count(IndexSegment.id)).where(
                            IndexSegment.document_version_id == version_id,
                            IndexSegment.model_config_id == model_config_id,
                        )
                    )
                    or 0
                )
                if count == job.segment_count and count > 0:
                    return IndexingResult(
                        document_version_id=version_id,
                        model_config_id=model_config_id,
                        status="already_completed",
                        segment_count=count,
                        attempt_count=job.attempt_count,
                    )
                job.status = "failed"
                job.error_code = "incomplete_persisted_index"

            if job is not None and job.status == "running" and job.updated_at > stale_before:
                return IndexingResult(
                    document_version_id=version_id,
                    model_config_id=model_config_id,
                    status="in_progress",
                    segment_count=job.segment_count,
                    attempt_count=job.attempt_count,
                )

            if job is None:
                job = IndexingJob(
                    document_version_id=version_id,
                    model_config_id=model_config_id,
                    status="running",
                    attempt_count=1,
                    segment_count=0,
                    error_code=None,
                    started_at=now,
                    updated_at=now,
                    completed_at=None,
                )
                session.add(job)
            else:
                if job.attempt_count >= self.settings.indexing_max_attempts:
                    raise IndexingAttemptsExhaustedError("indexing retry limit reached")
                job.status = "running"
                job.attempt_count += 1
                job.segment_count = 0
                job.error_code = None
                job.started_at = now
                job.updated_at = now
                job.completed_at = None
            await session.flush()
            attempt_count = job.attempt_count

        return IndexingResult(
            document_version_id=version_id,
            model_config_id=model_config_id,
            status="completed",
            segment_count=0,
            attempt_count=attempt_count,
        )

    async def _load_source(self, version_id: UUID) -> tuple[str, list[Chunk]]:
        async with self.database.session() as session:
            version = await session.scalar(
                select(DocumentVersion)
                .join(Document)
                .where(DocumentVersion.id == version_id, Document.owner_id == owner_id())
            )
            if version is None:
                raise IndexingSourceNotFoundError("document version does not exist")
            chunks = list(
                (
                    await session.scalars(
                        select(Chunk)
                        .where(Chunk.document_version_id == version_id)
                        .order_by(Chunk.chunk_index)
                    )
                ).all()
            )
            return version.parsed_text, chunks

    async def _commit_segments(
        self,
        version_id: UUID,
        model_config_id: UUID,
        segments: list[PreparedSegment],
        vectors: list[tuple[float, ...]],
        attempt_count: int,
    ) -> IndexingResult:
        if len(segments) != len(vectors):
            raise IndexingError("segment and vector counts differ")
        now = datetime.now(UTC)
        async with self.database.session() as session, session.begin():
            await self._advisory_lock(session, version_id, model_config_id)
            job = await session.scalar(
                select(IndexingJob)
                .where(
                    IndexingJob.document_version_id == version_id,
                    IndexingJob.model_config_id == model_config_id,
                )
                .with_for_update()
            )
            if job is None or job.status != "running" or job.attempt_count != attempt_count:
                raise IndexingError("indexing job state changed before commit")
            await session.execute(
                delete(IndexSegment).where(
                    IndexSegment.document_version_id == version_id,
                    IndexSegment.model_config_id == model_config_id,
                )
            )
            session.add_all(
                [
                    IndexSegment(
                        chunk_id=segment.chunk_id,
                        document_version_id=segment.document_version_id,
                        model_config_id=model_config_id,
                        segment_index=segment.segment_index,
                        content=segment.content,
                        char_start=segment.char_start,
                        char_end=segment.char_end,
                        line_start=segment.line_start,
                        line_end=segment.line_end,
                        token_count=segment.token_count,
                        embedding=list(vector),
                    )
                    for segment, vector in zip(segments, vectors, strict=True)
                ]
            )
            job.status = "completed"
            job.segment_count = len(segments)
            job.error_code = None
            job.updated_at = now
            job.completed_at = now
            await session.flush()
            add_audit(
                session, "document.index", "document_version", version_id, count=len(segments)
            )
        logger.info(
            "indexing.completed version_id=%s segment_count=%d model_revision=%s",
            version_id,
            len(segments),
            self.provider.info.revision,
        )
        return IndexingResult(
            document_version_id=version_id,
            model_config_id=model_config_id,
            status="completed",
            segment_count=len(segments),
            attempt_count=attempt_count,
        )

    async def _mark_failed(self, version_id: UUID, model_config_id: UUID, error_code: str) -> None:
        try:
            async with self.database.session() as session, session.begin():
                await self._advisory_lock(session, version_id, model_config_id)
                job = await session.scalar(
                    select(IndexingJob)
                    .where(
                        IndexingJob.document_version_id == version_id,
                        IndexingJob.model_config_id == model_config_id,
                    )
                    .with_for_update()
                )
                if job is not None and job.status == "running":
                    job.status = "failed"
                    job.error_code = error_code
                    job.updated_at = datetime.now(UTC)
                    job.completed_at = datetime.now(UTC)
                    add_audit(
                        session,
                        "document.index",
                        "document_version",
                        version_id,
                        outcome="failed",
                        reason=error_code,
                    )
        except SQLAlchemyError:
            logger.error("indexing.failure_state_unavailable version_id=%s", version_id)

    async def _target_version_ids(
        self, *, document_key: str | None, include_historical: bool
    ) -> list[UUID]:
        statement = select(DocumentVersion.id).join(Document).where(Document.owner_id == owner_id())
        if document_key is not None:
            statement = statement.where(Document.document_key == document_key)
        if not include_historical:
            latest = (
                select(func.max(DocumentVersion.version_number))
                .where(DocumentVersion.document_id == Document.id)
                .correlate(Document)
                .scalar_subquery()
            )
            statement = statement.where(DocumentVersion.version_number == latest)
        statement = statement.order_by(Document.document_key, DocumentVersion.version_number)
        async with self.database.session() as session:
            return list((await session.scalars(statement)).all())

    @staticmethod
    async def _advisory_lock(session, version_id: UUID, model_config_id: UUID) -> None:  # type: ignore[no-untyped-def]
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"index:{version_id}:{model_config_id}"},
        )

    @staticmethod
    def _error_code(exc: Exception) -> str:
        code = getattr(exc, "code", None)
        if isinstance(code, str):
            return code[:80]
        category = getattr(exc, "category", None)
        if category is not None:
            return f"embedding_{category!s}"[:80]
        return "indexing_failed"
