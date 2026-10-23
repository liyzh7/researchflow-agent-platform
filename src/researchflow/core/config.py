"""Validated application settings loaded from environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

from researchflow import __version__

Environment = Literal["local", "test", "development", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Runtime settings.

    ``environment`` is intentionally required so a deployment cannot silently
    report the wrong operating mode. Local development obtains it from ``.env``.
    """

    model_config = SettingsConfigDict(
        env_prefix="RESEARCHFLOW_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    environment: Environment
    app_name: str = "ResearchFlow"
    app_version: str = __version__
    log_level: LogLevel = "INFO"
    llm_provider_mode: Literal["mock", "openai_compatible"] = "mock"
    llm_timeout_seconds: float = Field(default=5.0, gt=0, le=120)
    llm_base_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:18080/v1")
    llm_model: str = Field(default="local-model-not-configured", min_length=1, max_length=255)
    llm_api_key: SecretStr | None = None
    llm_allow_remote: bool = False

    database_host: str = "127.0.0.1"
    database_port: int = Field(default=55432, ge=1, le=65535)
    database_user: str = "researchflow"
    database_password: SecretStr
    database_name: str = "researchflow"
    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_max_overflow: int = Field(default=5, ge=0, le=50)
    database_connect_timeout_seconds: int = Field(default=3, ge=1, le=30)
    auth_session_ttl_seconds: int = Field(default=28800, ge=60, le=604800)
    auth_login_per_minute: int = Field(default=60, ge=1, le=120)
    llm_max_concurrency: int = Field(default=1, ge=1, le=8)
    llm_max_queue: int = Field(default=8, ge=0, le=64)
    observability_max_spans: int = Field(default=512, ge=32, le=4096)

    upload_max_bytes: int = Field(default=2 * 1024 * 1024, ge=1, le=20 * 1024 * 1024)
    upload_read_size: int = Field(default=64 * 1024, ge=1024, le=1024 * 1024)
    document_chunk_size: int = Field(default=800, ge=100, le=10000)
    document_chunk_overlap: int = Field(default=100, ge=0, le=5000)

    embedding_provider_mode: Literal["local"] = "local"
    embedding_model_name: str = "intfloat/multilingual-e5-small"
    embedding_model_revision: str = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
    embedding_dimension: int = Field(default=384, ge=1, le=2000)
    embedding_normalize: bool = True
    embedding_query_prefix: str = "query: "
    embedding_passage_prefix: str = "passage: "
    embedding_max_input_tokens: int = Field(default=512, ge=16, le=8192)
    embedding_segment_tokens: int = Field(default=480, ge=8, le=8192)
    embedding_segment_overlap_tokens: int = Field(default=32, ge=0, le=1024)
    embedding_batch_size: int = Field(default=16, ge=1, le=128)
    embedding_timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    embedding_max_concurrency: int = Field(default=1, ge=1, le=8)
    embedding_cpu_threads: int = Field(default=4, ge=1, le=64)
    embedding_cache_dir: Path = Path(".cache/huggingface")
    embedding_local_files_only: bool = True

    indexing_max_attempts: int = Field(default=3, ge=1, le=10)
    indexing_stale_after_seconds: int = Field(default=600, ge=30, le=86400)
    retrieval_max_query_chars: int = Field(default=1000, ge=10, le=10000)
    retrieval_max_top_k: int = Field(default=50, ge=1, le=200)
    retrieval_candidate_multiplier: int = Field(default=4, ge=1, le=20)
    retrieval_rrf_k: int = Field(default=60, ge=1, le=1000)
    retrieval_hnsw_ef_search: int = Field(default=100, ge=1, le=1000)

    rag_enabled: bool = False
    agent_enabled: bool = False
    agent_access_secret: SecretStr | None = None
    agent_approval_ttl_seconds: int = Field(default=86400, ge=60, le=604800)
    agent_tool_timeout_seconds: float = Field(default=240, gt=0, le=600)
    agent_max_tool_calls: int = Field(default=8, ge=1, le=20)
    reranker_enabled: bool = False
    reranker_model_name: str = "BAAI/bge-reranker-base"
    reranker_model_revision: str = "2cfc18c9415c912f9d8155881c133215df768a70"
    reranker_max_input_tokens: int = Field(default=512, ge=32, le=8192)
    reranker_batch_size: int = Field(default=4, ge=1, le=64)
    reranker_candidate_limit: int = Field(default=12, ge=1, le=100)
    reranker_timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    reranker_max_concurrency: int = Field(default=1, ge=1, le=4)
    reranker_cpu_threads: int = Field(default=4, ge=1, le=64)
    reranker_cache_dir: Path = Path(".cache/huggingface")
    reranker_local_files_only: bool = True
    evidence_min_rerank_score: float | None = None
    evidence_min_query_coverage: float = Field(default=0.2, ge=0, le=1)
    evidence_max_items: int = Field(default=5, ge=1, le=20)
    rag_context_token_budget: int = Field(default=3000, ge=128, le=32000)

    @model_validator(mode="after")
    def validate_chunking(self) -> "Settings":
        if self.agent_enabled:
            if not self.rag_enabled:
                raise ValueError("agent_enabled requires rag_enabled")
            if (
                self.agent_access_secret is None
                or len(self.agent_access_secret.get_secret_value()) < 32
            ):
                raise ValueError("agent_enabled requires a secret with at least 32 characters")
        if self.document_chunk_overlap >= self.document_chunk_size:
            raise ValueError("document_chunk_overlap must be smaller than document_chunk_size")
        if self.embedding_segment_tokens >= self.embedding_max_input_tokens:
            raise ValueError("embedding_segment_tokens must leave room for prefixes and specials")
        if self.embedding_segment_overlap_tokens >= self.embedding_segment_tokens:
            raise ValueError(
                "embedding_segment_overlap_tokens must be smaller than embedding_segment_tokens"
            )
        if self.rag_enabled and not self.reranker_enabled:
            raise ValueError("rag_enabled requires reranker_enabled")
        if self.rag_enabled and self.evidence_min_rerank_score is None:
            raise ValueError("rag_enabled requires a calibrated evidence_min_rerank_score")
        if self.reranker_candidate_limit > self.retrieval_max_top_k:
            raise ValueError("reranker_candidate_limit must not exceed retrieval_max_top_k")
        return self

    def database_url(self, *, database_name: str | None = None) -> URL:
        """Build a structured URL so credentials are escaped and masked by default."""

        return URL.create(
            drivername="postgresql+psycopg",
            username=self.database_user,
            password=self.database_password.get_secret_value(),
            host=self.database_host,
            port=self.database_port,
            database=database_name or self.database_name,
        )


@lru_cache
def get_settings() -> Settings:
    """Load and cache process-wide settings."""

    return Settings()  # type: ignore[call-arg]
