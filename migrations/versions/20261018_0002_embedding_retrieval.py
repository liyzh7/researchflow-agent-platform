"""Add tokenizer-safe embedding segments and hybrid retrieval indexes.

Revision ID: 20261018_0002
Revises: 20261018_0001
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects import postgresql

revision: str = "20261018_0002"
down_revision: str | Sequence[str] | None = "20261018_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_unique_constraint("uq_chunks_id_version", "chunks", ["id", "document_version_id"])

    op.create_table(
        "embedding_model_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("config_key", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column("revision", sa.String(length=64), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("normalized", sa.Boolean(), nullable=False),
        sa.Column("query_prefix", sa.String(length=50), nullable=False),
        sa.Column("passage_prefix", sa.String(length=50), nullable=False),
        sa.Column("max_input_tokens", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "dimension = 384", name="ck_embedding_model_configs_supported_dimension"
        ),
        sa.CheckConstraint(
            "max_input_tokens > 0",
            name="ck_embedding_model_configs_positive_max_input_tokens",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_embedding_model_configs"),
        sa.UniqueConstraint("config_key", name="uq_embedding_model_configs_config_key"),
    )

    op.create_table(
        "index_segments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_config_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("segment_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("line_start", sa.Integer(), nullable=False),
        sa.Column("line_end", sa.Integer(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("embedding", VECTOR(384), nullable=False),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple'::regconfig, content)", persisted=True),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("char_end > char_start", name="ck_index_segments_valid_char_range"),
        sa.CheckConstraint("char_start >= 0", name="ck_index_segments_nonnegative_char_start"),
        sa.CheckConstraint("length(content) > 0", name="ck_index_segments_nonempty_content"),
        sa.CheckConstraint("line_end >= line_start", name="ck_index_segments_valid_line_range"),
        sa.CheckConstraint("line_start >= 1", name="ck_index_segments_positive_line_start"),
        sa.CheckConstraint(
            "segment_index >= 0", name="ck_index_segments_nonnegative_segment_index"
        ),
        sa.CheckConstraint(
            "token_count > 0 AND token_count <= 512",
            name="ck_index_segments_valid_token_count",
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id", "document_version_id"],
            ["chunks.id", "chunks.document_version_id"],
            name="fk_index_segments_chunk_version_chunks",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            name="fk_index_segments_document_version_id_document_versions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["model_config_id"],
            ["embedding_model_configs.id"],
            name="fk_index_segments_model_config_id_embedding_model_configs",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_index_segments"),
        sa.UniqueConstraint(
            "model_config_id",
            "chunk_id",
            "segment_index",
            name="uq_index_segments_model_chunk_index",
        ),
    )
    op.create_index(
        "ix_index_segments_version_model",
        "index_segments",
        ["document_version_id", "model_config_id"],
    )
    op.create_index(
        "ix_index_segments_embedding_hnsw",
        "index_segments",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(
        "ix_index_segments_search_vector_gin",
        "index_segments",
        ["search_vector"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_index_segments_content_trgm_gin",
        "index_segments",
        ["content"],
        postgresql_using="gin",
        postgresql_ops={"content": "gin_trgm_ops"},
    )

    op.create_table(
        "indexing_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_config_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("segment_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempt_count > 0", name="ck_indexing_jobs_positive_attempt_count"),
        sa.CheckConstraint("segment_count >= 0", name="ck_indexing_jobs_nonnegative_segment_count"),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_indexing_jobs_valid_indexing_status",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            name="fk_indexing_jobs_document_version_id_document_versions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["model_config_id"],
            ["embedding_model_configs.id"],
            name="fk_indexing_jobs_model_config_id_embedding_model_configs",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_indexing_jobs"),
        sa.UniqueConstraint(
            "document_version_id",
            "model_config_id",
            name="uq_indexing_jobs_version_model",
        ),
    )
    op.create_index("ix_indexing_jobs_status_updated", "indexing_jobs", ["status", "updated_at"])


def downgrade() -> None:
    op.drop_table("indexing_jobs")
    op.drop_table("index_segments")
    op.drop_table("embedding_model_configs")
    op.drop_constraint("uq_chunks_id_version", "chunks", type_="unique")
    # pg_trgm may be shared with later search features, so it remains installed.
