"""PostgreSQL current-version retrieval with cosine, FTS/trigram, and RRF modes."""

import logging
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from researchflow.auth.context import owner_id
from researchflow.core.config import Settings
from researchflow.db.database import Database
from researchflow.db.models import EmbeddingModelConfig
from researchflow.observability.tracing import traced
from researchflow.providers.embedding import (
    EmbeddingProvider,
    EmbeddingRequest,
    embedding_config_key,
    validate_embedding_vectors,
)
from researchflow.retrieval.errors import RetrievalError
from researchflow.retrieval.rrf import reciprocal_rank_fusion
from researchflow.retrieval.types import RetrievalHit, RetrievalResult, SearchMode

logger = logging.getLogger("researchflow.retrieval")

_ELIGIBLE_CTE = """
WITH current_versions AS (
    SELECT DISTINCT ON (dv.document_id)
        dv.id,
        dv.document_id,
        dv.version_number
    FROM document_versions AS dv
    ORDER BY dv.document_id, dv.version_number DESC
),
eligible AS (
    SELECT
        s.id AS segment_id,
        s.chunk_id,
        s.document_version_id AS version_id,
        s.segment_index,
        s.content,
        s.char_start,
        s.char_end,
        s.line_start,
        s.line_end,
        s.embedding,
        s.search_vector,
        c.chunk_index,
        d.id AS document_id,
        d.document_key,
        d.title,
        cv.version_number
    FROM index_segments AS s
    JOIN chunks AS c ON c.id = s.chunk_id
    JOIN current_versions AS cv ON cv.id = s.document_version_id
    JOIN documents AS d ON d.id = cv.document_id
    JOIN indexing_jobs AS j
      ON j.document_version_id = s.document_version_id
     AND j.model_config_id = s.model_config_id
     AND j.status = 'completed'
     AND j.segment_count > 0
    WHERE s.model_config_id = CAST(:model_config_id AS uuid)
      AND d.owner_id = CAST(:owner_id AS uuid)
      AND (CAST(:document_id AS uuid) IS NULL OR d.id = CAST(:document_id AS uuid))
      AND (CAST(:document_key AS text) IS NULL OR d.document_key = :document_key)
)
"""

_VECTOR_SQL = (
    _ELIGIBLE_CTE
    + """
SELECT
    eligible.*,
    embedding <=> CAST(:embedding AS vector) AS vector_distance
FROM eligible
ORDER BY embedding <=> CAST(:embedding AS vector), segment_id
LIMIT :candidate_limit
"""
)

_KEYWORD_SQL = (
    _ELIGIBLE_CTE
    + r"""
, query_value AS (
    SELECT websearch_to_tsquery('simple', :query) AS tsquery
)
SELECT
    eligible.*,
    CASE WHEN content ILIKE :substring_pattern ESCAPE '\' THEN 1 ELSE 0 END AS exact_match,
    ts_rank_cd(search_vector, query_value.tsquery) AS fts_rank,
    similarity(content, :query) AS trigram_score
FROM eligible
CROSS JOIN query_value
WHERE search_vector @@ query_value.tsquery
   OR content ILIKE :substring_pattern ESCAPE '\'
ORDER BY exact_match DESC, fts_rank DESC, trigram_score DESC, segment_id
LIMIT :candidate_limit
"""
)

_HAS_INDEX_SQL = _ELIGIBLE_CTE + "SELECT EXISTS(SELECT 1 FROM eligible)"


class RetrievalService:
    def __init__(
        self,
        database: Database,
        provider: EmbeddingProvider,
        settings: Settings,
    ) -> None:
        self.database = database
        self.provider = provider
        self.settings = settings

    async def search(
        self,
        *,
        query: str,
        top_k: int,
        mode: SearchMode,
        document_id: UUID | None = None,
        document_key: str | None = None,
    ) -> RetrievalResult:
        owner_id()
        candidate_limit = min(
            self.settings.retrieval_max_top_k * self.settings.retrieval_candidate_multiplier,
            top_k * self.settings.retrieval_candidate_multiplier,
        )
        model_config_id = await self._model_config_id()
        logger.info(
            "retrieval.started mode=%s query_chars=%d top_k=%d filtered=%s",
            mode,
            len(query),
            top_k,
            document_id is not None or document_key is not None,
        )
        if model_config_id is None or not await self._has_index(
            model_config_id, document_id, document_key
        ):
            return RetrievalResult(
                status="no_indexed_documents", mode=mode, items=(), model=self.provider.info
            )

        if mode == "keyword":
            keyword_hits = await self._keyword_search(
                query,
                candidate_limit,
                model_config_id,
                document_id,
                document_key,
            )
            return RetrievalResult(
                status="ok",
                mode=mode,
                items=tuple(keyword_hits[:top_k]),
                model=self.provider.info,
            )

        vector_hits = await self._vector_search(
            query,
            candidate_limit,
            model_config_id,
            document_id,
            document_key,
            approximate=True,
        )
        if mode == "vector":
            return RetrievalResult(
                status="ok", mode=mode, items=tuple(vector_hits[:top_k]), model=self.provider.info
            )

        keyword_hits = await self._keyword_search(
            query,
            candidate_limit,
            model_config_id,
            document_id,
            document_key,
        )
        fused = reciprocal_rank_fusion(
            vector_hits,
            keyword_hits,
            rrf_k=self.settings.retrieval_rrf_k,
            top_k=top_k,
        )
        return RetrievalResult(
            status="ok",
            mode="hybrid",
            items=tuple(fused),
            model=self.provider.info,
            rrf_k=self.settings.retrieval_rrf_k,
        )

    async def vector_search_exact(
        self,
        *,
        query: str,
        top_k: int,
        document_id: UUID | None = None,
        document_key: str | None = None,
    ) -> list[RetrievalHit]:
        owner_id()
        model_config_id = await self._model_config_id()
        if model_config_id is None:
            return []
        return await self._vector_search(
            query,
            top_k,
            model_config_id,
            document_id,
            document_key,
            approximate=False,
        )

    async def explain_vector_query(
        self,
        *,
        query: str,
        top_k: int,
        approximate: bool,
    ) -> list[str]:
        model_config_id = await self._model_config_id()
        if model_config_id is None:
            return []
        vector = await self._query_vector(query)
        parameters = self._parameters(
            model_config_id,
            None,
            None,
            candidate_limit=top_k,
            embedding=self._vector_literal(vector),
        )
        explain_sql = "EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) " + _VECTOR_SQL
        try:
            async with self.database.session() as session, session.begin():
                await self._configure_vector_scan(session, approximate)
                rows = (await session.execute(text(explain_sql), parameters)).all()
        except SQLAlchemyError as exc:
            raise RetrievalError("database vector explain failed") from exc
        return [str(row[0]) for row in rows]

    @traced("retrieval.vector.database")
    async def _vector_search(
        self,
        query: str,
        candidate_limit: int,
        model_config_id: UUID,
        document_id: UUID | None,
        document_key: str | None,
        *,
        approximate: bool,
    ) -> list[RetrievalHit]:
        vector = await self._query_vector(query)
        parameters = self._parameters(
            model_config_id,
            document_id,
            document_key,
            candidate_limit=candidate_limit,
            embedding=self._vector_literal(vector),
        )
        try:
            async with self.database.session() as session, session.begin():
                await self._configure_vector_scan(session, approximate)
                rows = (await session.execute(text(_VECTOR_SQL), parameters)).mappings().all()
        except SQLAlchemyError as exc:
            raise RetrievalError("database vector search failed") from exc
        return [
            self._hit(
                row, "vector", vector_rank=rank, vector_distance=float(row["vector_distance"])
            )
            for rank, row in enumerate(rows, start=1)
        ]

    @traced("retrieval.keyword.database")
    async def _keyword_search(
        self,
        query: str,
        candidate_limit: int,
        model_config_id: UUID,
        document_id: UUID | None,
        document_key: str | None,
    ) -> list[RetrievalHit]:
        parameters = self._parameters(
            model_config_id,
            document_id,
            document_key,
            candidate_limit=candidate_limit,
            query=query,
            substring_pattern=self._substring_pattern(query),
        )
        try:
            async with self.database.session() as session:
                rows = (await session.execute(text(_KEYWORD_SQL), parameters)).mappings().all()
        except SQLAlchemyError as exc:
            raise RetrievalError("database keyword search failed") from exc
        hits: list[RetrievalHit] = []
        for rank, row in enumerate(rows, start=1):
            score = max(
                float(row["exact_match"]),
                float(row["fts_rank"] or 0.0),
                float(row["trigram_score"] or 0.0),
            )
            hits.append(self._hit(row, "keyword", keyword_rank=rank, keyword_score=score))
        return hits

    async def _query_vector(self, query: str) -> tuple[float, ...]:
        response = await self.provider.embed_queries(
            EmbeddingRequest(texts=(query,)),
            timeout_seconds=self.settings.embedding_timeout_seconds,
        )
        if response.model != self.provider.info or len(response.vectors) != 1:
            raise RetrievalError("embedding provider returned inconsistent query metadata")
        return validate_embedding_vectors(response.vectors, self.provider.info, expected_count=1)[0]

    async def _model_config_id(self) -> UUID | None:
        key = embedding_config_key(self.provider.info)
        try:
            async with self.database.session() as session:
                return await session.scalar(
                    select(EmbeddingModelConfig.id).where(EmbeddingModelConfig.config_key == key)
                )
        except SQLAlchemyError as exc:
            raise RetrievalError("embedding configuration lookup failed") from exc

    async def _has_index(
        self,
        model_config_id: UUID,
        document_id: UUID | None,
        document_key: str | None,
    ) -> bool:
        parameters = self._parameters(model_config_id, document_id, document_key)
        try:
            async with self.database.session() as session:
                return bool(await session.scalar(text(_HAS_INDEX_SQL), parameters))
        except SQLAlchemyError as exc:
            raise RetrievalError("indexed document lookup failed") from exc

    async def _configure_vector_scan(self, session, approximate: bool) -> None:  # type: ignore[no-untyped-def]
        if approximate:
            await session.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
            await session.execute(
                text(f"SET LOCAL hnsw.ef_search = {self.settings.retrieval_hnsw_ef_search}")
            )
        else:
            await session.execute(text("SET LOCAL enable_indexscan = off"))
            await session.execute(text("SET LOCAL enable_bitmapscan = off"))

    @staticmethod
    def _parameters(
        model_config_id: UUID,
        document_id: UUID | None,
        document_key: str | None,
        **extra: object,
    ) -> dict[str, object]:
        return {
            "owner_id": str(owner_id()),
            "model_config_id": str(model_config_id),
            "document_id": str(document_id) if document_id is not None else None,
            "document_key": document_key,
            **extra,
        }

    @staticmethod
    def _substring_pattern(query: str) -> str:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{escaped}%"

    @staticmethod
    def _vector_literal(vector: tuple[float, ...]) -> str:
        return "[" + ",".join(format(value, ".9g") for value in vector) + "]"

    @staticmethod
    def _hit(row, method: SearchMode, **scores: object) -> RetrievalHit:  # type: ignore[no-untyped-def]
        return RetrievalHit(
            document_id=row["document_id"],
            document_key=row["document_key"],
            title=row["title"],
            version_id=row["version_id"],
            version_number=row["version_number"],
            chunk_id=row["chunk_id"],
            chunk_index=row["chunk_index"],
            segment_id=row["segment_id"],
            segment_index=row["segment_index"],
            content=row["content"],
            char_start=row["char_start"],
            char_end=row["char_end"],
            line_start=row["line_start"],
            line_end=row["line_end"],
            retrieval_method=method,
            **scores,
        )
