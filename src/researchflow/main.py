"""ResearchFlow FastAPI application factory and ASGI entry point."""

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Response

from researchflow.agent.checkpoint import postgres_checkpointer
from researchflow.agent.repository import AgentRepository
from researchflow.agent.service import AgentService
from researchflow.agent.tools import AgentTools
from researchflow.api.dependencies import get_database
from researchflow.api.router import api_router
from researchflow.api.schemas import HealthResponse, ReadinessResponse
from researchflow.auth.service import AuthService
from researchflow.core.asyncio_compat import configure_asyncio_policy
from researchflow.core.body_limit import BodyLimitMiddleware
from researchflow.core.config import Settings, get_settings
from researchflow.core.errors import ErrorResponse, register_exception_handlers
from researchflow.core.logging import add_request_logging_middleware, configure_logging
from researchflow.core.resources import drain_workers
from researchflow.db.database import Database
from researchflow.documents.chunker import CharacterChunker
from researchflow.documents.parsers import ParserRegistry
from researchflow.documents.service import DocumentService
from researchflow.indexing.service import IndexingService
from researchflow.observability.llm import ObservedLLMProvider
from researchflow.observability.tracing import Telemetry
from researchflow.providers.embedding import EmbeddingProvider
from researchflow.providers.llm import LLMProvider
from researchflow.providers.local_embedding import LocalEmbeddingProvider
from researchflow.providers.mock import MockLLMProvider
from researchflow.providers.openai_compatible import OpenAICompatibleLLMProvider
from researchflow.rag.citations import (
    CitationService,
    EvidenceSourceRepository,
    PostgresEvidenceSourceRepository,
)
from researchflow.rag.evidence import EvidenceGate
from researchflow.rag.prompt import RAGPromptBuilder
from researchflow.rag.service import RAGService
from researchflow.reranking.cross_encoder import LocalCrossEncoderReranker
from researchflow.reranking.service import RerankService
from researchflow.reranking.types import RerankerProvider
from researchflow.retrieval.service import RetrievalService
from researchflow.services.chat import ChatService

logger = logging.getLogger("researchflow.lifecycle")

configure_asyncio_policy()


def create_app(
    settings: Settings | None = None,
    llm_provider: LLMProvider | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    database: Database | None = None,
    reranker_provider: RerankerProvider | None = None,
    evidence_source_repository: EvidenceSourceRepository | None = None,
) -> FastAPI:
    """Create an isolated application instance, allowing test-time injection."""

    runtime_settings = settings or get_settings()
    configure_logging(runtime_settings.log_level)
    raw_provider = llm_provider or _build_llm_provider(runtime_settings)
    runtime_embedding_provider = embedding_provider or LocalEmbeddingProvider(runtime_settings)
    runtime_database = database or Database(runtime_settings)
    provider = ObservedLLMProvider(raw_provider, runtime_database, runtime_settings)
    telemetry = Telemetry(runtime_settings.observability_max_spans)
    document_service = DocumentService(
        runtime_database,
        parser_registry=ParserRegistry(),
        chunker=CharacterChunker(
            chunk_size=runtime_settings.document_chunk_size,
            overlap=runtime_settings.document_chunk_overlap,
        ),
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "application.started environment=%s provider=%s",
            runtime_settings.environment,
            provider.name,
        )
        try:
            async with AsyncExitStack() as stack:
                if runtime_settings.agent_enabled:
                    saver = await stack.enter_async_context(postgres_checkpointer(runtime_settings))
                    repository = AgentRepository(
                        runtime_database, runtime_settings, CitationService(citation_repository)
                    )
                    application.state.agent_service = AgentService(
                        repository,
                        AgentTools(
                            application.state.rag_service, provider, repository, runtime_settings
                        ),
                        saver,
                    )
                yield
        finally:
            remaining_workers = await drain_workers()
            if remaining_workers:
                logger.warning("shutdown.native_workers_pending count=%d", remaining_workers)
            await runtime_database.dispose()
            telemetry.close()
            logger.info("application.stopped")

    application = FastAPI(
        title=runtime_settings.app_name,
        version=runtime_settings.app_version,
        description="ResearchFlow: evidence-gated RAG and approval-gated durable workflows.",
        lifespan=lifespan,
        responses={
            422: {"model": ErrorResponse, "description": "Request validation failed"},
            500: {"model": ErrorResponse, "description": "Internal server error"},
        },
    )
    application.state.settings = runtime_settings
    application.state.telemetry = telemetry
    application.state.auth_service = AuthService(runtime_database, runtime_settings)
    application.state.llm_provider = provider
    application.state.database = runtime_database
    application.state.document_service = document_service
    application.state.embedding_provider = runtime_embedding_provider
    application.state.indexing_service = IndexingService(
        runtime_database,
        runtime_embedding_provider,
        runtime_settings,
    )
    application.state.retrieval_service = RetrievalService(
        runtime_database,
        runtime_embedding_provider,
        runtime_settings,
    )
    runtime_reranker_provider = reranker_provider
    if runtime_reranker_provider is None and runtime_settings.reranker_enabled:
        runtime_reranker_provider = LocalCrossEncoderReranker(runtime_settings)
    rerank_service = (
        RerankService(
            runtime_reranker_provider,
            timeout_seconds=runtime_settings.reranker_timeout_seconds,
        )
        if runtime_reranker_provider is not None
        else None
    )
    citation_repository = evidence_source_repository or PostgresEvidenceSourceRepository(
        runtime_database
    )
    application.state.rag_service = RAGService(
        settings=runtime_settings,
        retrieval=application.state.retrieval_service,
        reranker=rerank_service,
        gate=EvidenceGate(
            min_rerank_score=(
                runtime_settings.evidence_min_rerank_score
                if runtime_settings.evidence_min_rerank_score is not None
                else float("inf")
            ),
            min_query_coverage=runtime_settings.evidence_min_query_coverage,
            max_items=runtime_settings.evidence_max_items,
        ),
        prompt_builder=RAGPromptBuilder(
            context_token_budget=runtime_settings.rag_context_token_budget
        ),
        provider=provider,
        citations=CitationService(citation_repository),
    )
    application.state.chat_service = ChatService(
        provider,
        timeout_seconds=runtime_settings.llm_timeout_seconds,
    )

    register_exception_handlers(application)
    application.add_middleware(
        BodyLimitMiddleware, upload_max_bytes=runtime_settings.upload_max_bytes
    )
    add_request_logging_middleware(application)

    @application.get("/health", response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        return HealthResponse()

    @application.get("/ready", response_model=ReadinessResponse, tags=["system"])
    async def ready(
        response: Response,
        database_dependency: Annotated[Database, Depends(get_database)],
    ) -> ReadinessResponse:
        result = await database_dependency.readiness()
        if not result.ready:
            response.status_code = 503
        return ReadinessResponse(
            status="ready" if result.ready else "not_ready",
            database=result.database,  # type: ignore[arg-type]
            pgvector_version=result.pgvector_version,
        )

    application.include_router(api_router)
    return application


def _build_llm_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider_mode == "openai_compatible":
        return OpenAICompatibleLLMProvider(settings)
    return MockLLMProvider()


app = create_app()
