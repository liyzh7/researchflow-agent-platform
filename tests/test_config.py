"""Configuration validation tests."""

import asyncio

import pytest
from pydantic import ValidationError

from researchflow.core.asyncio_compat import selector_loop_factory
from researchflow.core.config import Settings


def test_missing_required_environment_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RESEARCHFLOW_ENVIRONMENT", raising=False)
    monkeypatch.delenv("RESEARCHFLOW_DATABASE_PASSWORD", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)  # type: ignore[call-arg]

    errors = str(exc_info.value)
    assert "environment" in errors
    assert "database_password" in errors


def test_invalid_provider_mode_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            database_password="unit-test-password",
            llm_provider_mode="real",  # type: ignore[arg-type]
            _env_file=None,
        )


def test_overlap_must_be_smaller_than_chunk_size() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            database_password="unit-test-password",
            document_chunk_size=100,
            document_chunk_overlap=100,
            _env_file=None,
        )


def test_database_url_masks_password() -> None:
    settings = Settings(
        environment="test",
        database_password="not-for-logs",
        _env_file=None,
    )

    assert "not-for-logs" not in str(settings.database_url())
    assert "***" in str(settings.database_url())


def test_rag_requires_enabled_reranker_and_calibrated_threshold() -> None:
    with pytest.raises(ValidationError, match="reranker_enabled"):
        Settings(
            environment="test",
            database_password="unit-test-password",
            rag_enabled=True,
            _env_file=None,
        )
    with pytest.raises(ValidationError, match="evidence_min_rerank_score"):
        Settings(
            environment="test",
            database_password="unit-test-password",
            rag_enabled=True,
            reranker_enabled=True,
            _env_file=None,
        )


def test_reranker_candidate_limit_must_fit_retrieval_limit() -> None:
    with pytest.raises(ValidationError, match="reranker_candidate_limit"):
        Settings(
            environment="test",
            database_password="unit-test-password",
            retrieval_max_top_k=5,
            reranker_candidate_limit=6,
            _env_file=None,
        )


def test_server_loop_factory_uses_selector_loop() -> None:
    loop = selector_loop_factory()
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
    finally:
        loop.close()


@pytest.mark.parametrize("secret", [None, "too-short"])
def test_agent_requires_long_capability_secret(secret) -> None:
    with pytest.raises(ValidationError, match="at least 32 characters"):
        Settings(
            environment="test",
            database_password="unit-test-password",
            agent_enabled=True,
            rag_enabled=True,
            reranker_enabled=True,
            evidence_min_rerank_score=0,
            agent_access_secret=secret,
            _env_file=None,
        )


def test_agent_requires_existing_rag_safety_pipeline() -> None:
    with pytest.raises(ValidationError, match="requires rag_enabled"):
        Settings(
            environment="test",
            database_password="unit-test-password",
            agent_enabled=True,
            agent_access_secret="test-only-capability-secret-with-32-characters",
            _env_file=None,
        )
