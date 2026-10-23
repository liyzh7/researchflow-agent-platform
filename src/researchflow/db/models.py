"""Relational persistence model for versioned research documents."""

from datetime import datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from researchflow.agent import models as agent_models  # noqa: F401
from researchflow.auth import models as auth_models  # noqa: F401
from researchflow.db.base import Base
from researchflow.observability import models as observation_models  # noqa: F401


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    __table_args__ = (UniqueConstraint("owner_id", "document_key", name="uq_documents_owner_key"),)
    owner_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("app_users.id", ondelete="RESTRICT"), index=True
    )
    document_key: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version_number", name="uq_document_versions_number"),
        CheckConstraint("version_number > 0", name="positive_version_number"),
        CheckConstraint("file_size > 0", name="positive_file_size"),
        Index("ix_document_versions_document_created", "document_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    parsed_text: Mapped[str] = mapped_column(Text, nullable=False)
    parser_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    document: Mapped[Document] = relationship(back_populates="versions")
    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document_version", cascade="all, delete-orphan"
    )
    index_segments: Mapped[list["IndexSegment"]] = relationship(
        back_populates="document_version", viewonly=True
    )
    indexing_jobs: Mapped[list["IndexingJob"]] = relationship(
        back_populates="document_version", cascade="all, delete-orphan"
    )


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_version_id", "chunk_index", name="uq_chunks_version_index"),
        CheckConstraint("chunk_index >= 0", name="nonnegative_chunk_index"),
        CheckConstraint("char_start >= 0", name="nonnegative_char_start"),
        CheckConstraint("char_end > char_start", name="valid_char_range"),
        CheckConstraint("line_start >= 1", name="positive_line_start"),
        CheckConstraint("line_end >= line_start", name="valid_line_range"),
        CheckConstraint("length(content) > 0", name="nonempty_content"),
        UniqueConstraint("id", "document_version_id", name="uq_chunks_id_version"),
        Index("ix_chunks_version_order", "document_version_id", "chunk_index"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    line_start: Mapped[int] = mapped_column(Integer, nullable=False)
    line_end: Mapped[int] = mapped_column(Integer, nullable=False)
    chunker_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    document_version: Mapped[DocumentVersion] = relationship(back_populates="chunks")
    index_segments: Mapped[list["IndexSegment"]] = relationship(
        back_populates="chunk", cascade="all, delete-orphan"
    )


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"
    owner_id: Mapped[UUID | None] = mapped_column(ForeignKey("app_users.id", ondelete="RESTRICT"))
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'completed', 'failed')", name="valid_ingestion_status"
        ),
        Index("ix_ingestion_jobs_document_key_created", "document_key", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_key: Mapped[str] = mapped_column(String(128), nullable=False)
    document_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    document_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    is_duplicate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EmbeddingModelConfig(Base):
    """Immutable identity for an embedding model and its retrieval semantics."""

    __tablename__ = "embedding_model_configs"
    __table_args__ = (
        CheckConstraint("dimension = 384", name="supported_dimension"),
        CheckConstraint("max_input_tokens > 0", name="positive_max_input_tokens"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    config_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    revision: Mapped[str] = mapped_column(String(64), nullable=False)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    normalized: Mapped[bool] = mapped_column(Boolean, nullable=False)
    query_prefix: Mapped[str] = mapped_column(String(50), nullable=False)
    passage_prefix: Mapped[str] = mapped_column(String(50), nullable=False)
    max_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    index_segments: Mapped[list["IndexSegment"]] = relationship(back_populates="model_config")
    indexing_jobs: Mapped[list["IndexingJob"]] = relationship(back_populates="model_config")


class IndexSegment(Base):
    """Tokenizer-safe searchable slice mapped to an immutable source chunk."""

    __tablename__ = "index_segments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["chunk_id", "document_version_id"],
            ["chunks.id", "chunks.document_version_id"],
            name="fk_index_segments_chunk_version_chunks",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "model_config_id",
            "chunk_id",
            "segment_index",
            name="uq_index_segments_model_chunk_index",
        ),
        CheckConstraint("segment_index >= 0", name="nonnegative_segment_index"),
        CheckConstraint("char_start >= 0", name="nonnegative_char_start"),
        CheckConstraint("char_end > char_start", name="valid_char_range"),
        CheckConstraint("line_start >= 1", name="positive_line_start"),
        CheckConstraint("line_end >= line_start", name="valid_line_range"),
        CheckConstraint("token_count > 0 AND token_count <= 512", name="valid_token_count"),
        CheckConstraint("length(content) > 0", name="nonempty_content"),
        Index(
            "ix_index_segments_version_model",
            "document_version_id",
            "model_config_id",
        ),
        Index(
            "ix_index_segments_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index(
            "ix_index_segments_search_vector_gin",
            "search_vector",
            postgresql_using="gin",
        ),
        Index(
            "ix_index_segments_content_trgm_gin",
            "content",
            postgresql_using="gin",
            postgresql_ops={"content": "gin_trgm_ops"},
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    chunk_id: Mapped[UUID] = mapped_column(nullable=False)
    document_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False
    )
    model_config_id: Mapped[UUID] = mapped_column(
        ForeignKey("embedding_model_configs.id", ondelete="RESTRICT"), nullable=False
    )
    segment_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    line_start: Mapped[int] = mapped_column(Integer, nullable=False)
    line_end: Mapped[int] = mapped_column(Integer, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(VECTOR(384), nullable=False)
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple'::regconfig, content)", persisted=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    chunk: Mapped[Chunk] = relationship(back_populates="index_segments")
    document_version: Mapped[DocumentVersion] = relationship(
        back_populates="index_segments", viewonly=True
    )
    model_config: Mapped[EmbeddingModelConfig] = relationship(back_populates="index_segments")


class IndexingJob(Base):
    """One retryable state machine per document version and model configuration."""

    __tablename__ = "indexing_jobs"
    __table_args__ = (
        UniqueConstraint(
            "document_version_id", "model_config_id", name="uq_indexing_jobs_version_model"
        ),
        CheckConstraint(
            "status IN ('running', 'completed', 'failed')", name="valid_indexing_status"
        ),
        CheckConstraint("attempt_count > 0", name="positive_attempt_count"),
        CheckConstraint("segment_count >= 0", name="nonnegative_segment_count"),
        Index("ix_indexing_jobs_status_updated", "status", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False
    )
    model_config_id: Mapped[UUID] = mapped_column(
        ForeignKey("embedding_model_configs.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    segment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    document_version: Mapped[DocumentVersion] = relationship(back_populates="indexing_jobs")
    model_config: Mapped[EmbeddingModelConfig] = relationship(back_populates="indexing_jobs")
