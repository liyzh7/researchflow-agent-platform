"""Offline end-to-end RAG orchestration and API behavior."""

import logging

import pytest

from researchflow.core.config import Settings
from researchflow.main import create_app
from researchflow.providers.errors import LLMProviderTimeoutError
from researchflow.providers.mock import MockLLMProvider
from researchflow.rag.citations import CitationService
from researchflow.rag.evidence import EvidenceGate
from researchflow.rag.prompt import RAGPromptBuilder
from researchflow.rag.service import RAGService
from researchflow.rag.types import EvidenceReason, EvidenceStatus
from researchflow.reranking.service import RerankService
from researchflow.retrieval.errors import RetrievalError
from tests.auth_support import UnitTestClient as TestClient
from tests.support import (
    CountingStructuredProvider,
    DeterministicEmbeddingProvider,
    DeterministicReranker,
    InMemoryEvidenceSourceRepository,
    StubRetrievalService,
    make_retrieval_hit,
)


def rag_settings(**changes) -> Settings:  # type: ignore[no-untyped-def]
    return Settings(
        environment="test",
        database_password="test",
        rag_enabled=True,
        reranker_enabled=True,
        evidence_min_rerank_score=0.5,
        evidence_min_query_coverage=0.25,
        rag_context_token_budget=1000,
        _env_file=None,
        **changes,
    )


def build_service(
    *,
    hits,  # type: ignore[no-untyped-def]
    provider=None,  # type: ignore[no-untyped-def]
    settings=None,  # type: ignore[no-untyped-def]
    retrieval=None,  # type: ignore[no-untyped-def]
) -> RAGService:
    runtime_settings = settings or rag_settings()
    reranker = DeterministicReranker({hit.segment_id: 1.5 for hit in hits})
    return RAGService(
        settings=runtime_settings,
        retrieval=retrieval or StubRetrievalService(tuple(hits)),
        reranker=RerankService(reranker, timeout_seconds=1),
        gate=EvidenceGate(
            min_rerank_score=0.5,
            min_query_coverage=0.25,
            max_items=5,
        ),
        prompt_builder=RAGPromptBuilder(context_token_budget=1000),
        provider=provider or MockLLMProvider(),
        citations=CitationService(InMemoryEvidenceSourceRepository(tuple(hits))),
    )


@pytest.mark.anyio
async def test_mock_rag_answer_is_grounded_and_explicitly_mock() -> None:
    hit = make_retrieval_hit(
        "A toy shelf has two empty slots. Slot labels are kept in a local ledger."
    )
    result = await build_service(hits=(hit,)).answer(
        query="Which ledger keeps the toy shelf slot labels?",
        retrieval_mode="hybrid",
        top_k=3,
        rerank_enabled=True,
    )
    assert result.status == "answered"
    assert result.evidence_status is EvidenceStatus.ANSWERABLE
    assert result.is_mock is True
    assert result.answer.startswith("[MOCK]")
    assert result.citations[0].segment_id == hit.segment_id
    assert result.reranker_model == "test-reranker"


@pytest.mark.anyio
async def test_insufficient_evidence_refuses_without_calling_provider() -> None:
    provider = CountingStructuredProvider()
    service = build_service(hits=(), provider=provider)
    result = await service.answer(
        query="which tray holds the missing striped card",
        retrieval_mode="hybrid",
        top_k=3,
        rerank_enabled=True,
    )
    assert result.status == "refused"
    assert result.evidence_status is EvidenceStatus.INSUFFICIENT_EVIDENCE
    assert result.evidence_reasons == (EvidenceReason.NO_CANDIDATES,)
    assert result.citations == ()
    assert provider.call_count == 0


@pytest.mark.anyio
async def test_disabling_rerank_cannot_bypass_calibrated_gate() -> None:
    hit = make_retrieval_hit("toy shelf slot ledger")
    provider = CountingStructuredProvider()
    result = await build_service(hits=(hit,), provider=provider).answer(
        query="toy shelf slot ledger",
        retrieval_mode="hybrid",
        top_k=1,
        rerank_enabled=False,
    )
    assert result.status == "refused"
    assert result.evidence_reasons == (EvidenceReason.RERANK_REQUIRED,)
    assert provider.call_count == 0


@pytest.mark.anyio
async def test_invalid_structured_provider_output_is_rejected() -> None:
    hit = make_retrieval_hit("toy shelf slot ledger")
    service = build_service(hits=(hit,), provider=CountingStructuredProvider(content="not-json"))
    from researchflow.providers.errors import LLMProviderInvalidResponseError

    with pytest.raises(LLMProviderInvalidResponseError):
        await service.answer(
            query="toy shelf slot ledger",
            retrieval_mode="hybrid",
            top_k=1,
            rerank_enabled=True,
        )


def test_rag_endpoint_is_disabled_by_default(settings: Settings) -> None:
    with TestClient(create_app(settings=settings)) as client:
        response = client.post("/api/v1/rag/answer", json={"query": "question"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "rag_not_configured"


def test_rag_endpoint_returns_full_stable_response() -> None:
    hit = make_retrieval_hit(
        "A toy shelf has two empty slots. Slot labels are kept in a local ledger."
    )
    settings = rag_settings()
    app = create_app(
        settings=settings,
        embedding_provider=DeterministicEmbeddingProvider(),
        reranker_provider=DeterministicReranker(),
        evidence_source_repository=InMemoryEvidenceSourceRepository((hit,)),
    )
    app.state.rag_service = build_service(hits=(hit,), settings=settings)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/rag/answer",
            json={"query": "Which ledger keeps the toy shelf slot labels?", "top_k": 3},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "answered"
    assert body["is_mock"] is True
    assert body["evidence_status"] == "ANSWERABLE"
    assert body["citations"][0]["segment_id"] == str(hit.segment_id)
    assert set(body["timing"]) == {
        "retrieval_ms",
        "rerank_ms",
        "gate_ms",
        "generation_ms",
        "citation_validation_ms",
        "total_ms",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"query": ""},
        {"query": "x" * 1001},
        {
            "query": "valid",
            "document_key": "a",
            "document_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        },
    ],
)
def test_rag_endpoint_validates_input(settings: Settings, payload: dict[str, object]) -> None:
    with TestClient(create_app(settings=settings)) as client:
        response = client.post("/api/v1/rag/answer", json=payload)
    assert response.status_code == 422


def test_retrieval_failure_is_a_system_error_not_a_refusal() -> None:
    class BrokenRetrieval:
        async def search(self, **arguments):  # type: ignore[no-untyped-def]
            raise RetrievalError

    settings = rag_settings()
    hit = make_retrieval_hit("toy shelf slot ledger")
    app = create_app(
        settings=settings,
        embedding_provider=DeterministicEmbeddingProvider(),
        reranker_provider=DeterministicReranker(),
    )
    app.state.rag_service = build_service(
        hits=(hit,), settings=settings, retrieval=BrokenRetrieval()
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/v1/rag/answer", json={"query": "toy shelf slot ledger"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "retrieval_unavailable"


def test_provider_timeout_is_exposed_as_gateway_timeout() -> None:
    class TimeoutProvider(CountingStructuredProvider):
        async def generate(self, request, *, timeout_seconds):  # type: ignore[no-untyped-def]
            raise LLMProviderTimeoutError

    hit = make_retrieval_hit("toy shelf slot ledger")
    settings = rag_settings()
    app = create_app(
        settings=settings,
        embedding_provider=DeterministicEmbeddingProvider(),
        reranker_provider=DeterministicReranker(),
    )
    app.state.rag_service = build_service(
        hits=(hit,), settings=settings, provider=TimeoutProvider()
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/v1/rag/answer", json={"query": "toy shelf slot ledger"})
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "provider_timeout"


def test_logs_do_not_include_query_key_or_private_document(caplog) -> None:  # type: ignore[no-untyped-def]
    secret_query = "query-secret-7b1f"
    secret_key = "api-key-secret-9c2e"
    private_text = "private-document-secret-4d3a"
    hit = make_retrieval_hit(f"toy shelf slot ledger {private_text}")
    settings = rag_settings(llm_api_key=secret_key)
    app = create_app(
        settings=settings,
        embedding_provider=DeterministicEmbeddingProvider(),
        reranker_provider=DeterministicReranker(),
    )
    app.state.rag_service = build_service(hits=(hit,), settings=settings)
    with caplog.at_level(logging.INFO), TestClient(app) as client:
        response = client.post(
            "/api/v1/rag/answer",
            json={"query": f"toy shelf slot ledger {secret_query}"},
        )
    assert response.status_code == 200
    assert secret_query not in caplog.text
    assert secret_key not in caplog.text
    assert private_text not in caplog.text
