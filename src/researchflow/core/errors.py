"""Stable public error envelopes and FastAPI exception handlers."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from researchflow.agent.errors import AgentError
from researchflow.auth.context import AuthError
from researchflow.documents.errors import DocumentError
from researchflow.providers.errors import (
    EmbeddingErrorCategory,
    EmbeddingProviderError,
    LLMErrorCategory,
    LLMProviderBusyError,
    LLMProviderError,
    LLMProviderRedirectError,
    RerankerErrorCategory,
    RerankerProviderError,
)
from researchflow.rag.errors import RAGError
from researchflow.retrieval.errors import RetrievalError

logger = logging.getLogger("researchflow.error")


class ErrorDetail(BaseModel):
    """A sanitized validation detail safe to return to API clients."""

    field: str
    message: str
    type: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    error: ErrorBody


def _error_response(
    status_code: int,
    code: str,
    message: str,
    details: list[ErrorDetail] | None = None,
) -> JSONResponse:
    payload = ErrorResponse(error=ErrorBody(code=code, message=message, details=details or []))
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


def register_exception_handlers(app: FastAPI) -> None:
    """Install handlers that never expose request bodies or exception internals."""

    @app.exception_handler(AuthError)
    async def auth_error_handler(_request: Request, exc: AuthError) -> JSONResponse:
        response = _error_response(exc.status_code, exc.code, "Authentication or access denied.")
        if exc.status_code == 401:
            response.headers["WWW-Authenticate"] = "Bearer"
        if exc.status_code == 429:
            response.headers["Retry-After"] = "60"
        return response

    @app.exception_handler(AgentError)
    async def agent_error_handler(_request: Request, exc: AgentError) -> JSONResponse:
        return _error_response(exc.status_code, exc.code, "Workflow operation could not complete.")

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = [
            ErrorDetail(
                field=".".join(str(part) for part in error["loc"]),
                message=error["msg"],
                type=error["type"],
            )
            for error in exc.errors()
        ]
        return _error_response(
            status_code=422,
            code="validation_error",
            message="Request validation failed.",
            details=details,
        )

    @app.exception_handler(LLMProviderError)
    async def provider_error_handler(_request: Request, exc: LLMProviderError) -> JSONResponse:
        if isinstance(exc, LLMProviderBusyError):
            return _error_response(503, "provider_busy", "The local model queue is full.")
        if isinstance(exc, LLMProviderRedirectError):
            return _error_response(
                502,
                "provider_redirect_blocked",
                "Provider redirects are disabled. Configure the final endpoint URL.",
            )
        if exc.category is LLMErrorCategory.TIMEOUT:
            return _error_response(504, "provider_timeout", "The model provider timed out.")
        if exc.category is LLMErrorCategory.INVALID_REQUEST:
            return _error_response(500, "provider_configuration_error", "Provider request failed.")
        if exc.category is LLMErrorCategory.INVALID_RESPONSE:
            return _error_response(
                502, "provider_invalid_response", "Provider response was invalid."
            )
        if exc.category is LLMErrorCategory.AUTHENTICATION:
            return _error_response(503, "provider_authentication_error", "Provider access failed.")
        if exc.category is LLMErrorCategory.RATE_LIMITED:
            return _error_response(429, "provider_rate_limited", "The model provider is busy.")
        if exc.category is LLMErrorCategory.UPSTREAM:
            return _error_response(502, "provider_upstream_error", "The model provider failed.")
        return _error_response(503, "provider_unavailable", "The model provider is unavailable.")

    @app.exception_handler(DocumentError)
    async def document_error_handler(_request: Request, exc: DocumentError) -> JSONResponse:
        return _error_response(exc.status_code, exc.code, exc.public_message)

    @app.exception_handler(EmbeddingProviderError)
    async def embedding_error_handler(
        _request: Request, exc: EmbeddingProviderError
    ) -> JSONResponse:
        if exc.category is EmbeddingErrorCategory.TIMEOUT:
            return _error_response(504, "embedding_timeout", "Embedding inference timed out.")
        if exc.category in {
            EmbeddingErrorCategory.INVALID_INPUT,
            EmbeddingErrorCategory.INVALID_VECTOR,
        }:
            return _error_response(
                500, "embedding_validation_error", "Embedding validation failed."
            )
        return _error_response(503, "embedding_unavailable", "Embedding is unavailable.")

    @app.exception_handler(RetrievalError)
    async def retrieval_error_handler(_request: Request, exc: RetrievalError) -> JSONResponse:
        return _error_response(exc.status_code, exc.code, exc.public_message)

    @app.exception_handler(RerankerProviderError)
    async def reranker_error_handler(_request: Request, exc: RerankerProviderError) -> JSONResponse:
        if exc.category is RerankerErrorCategory.TIMEOUT:
            return _error_response(504, "reranker_timeout", "Reranking timed out.")
        if exc.category is RerankerErrorCategory.INVALID_INPUT:
            return _error_response(500, "reranker_configuration_error", "Reranking failed.")
        if exc.category is RerankerErrorCategory.INVALID_RESPONSE:
            return _error_response(
                502, "reranker_invalid_response", "Reranking output was invalid."
            )
        return _error_response(503, "reranker_unavailable", "Reranking is unavailable.")

    @app.exception_handler(RAGError)
    async def rag_error_handler(_request: Request, exc: RAGError) -> JSONResponse:
        return _error_response(exc.status_code, exc.code, exc.public_message)

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "not_found" if exc.status_code == 404 else "http_error"
        message = exc.detail if isinstance(exc.detail, str) else "HTTP request failed."
        return _error_response(exc.status_code, code, message)

    @app.exception_handler(Exception)
    async def unexpected_error_handler(_request: Request, exc: Exception) -> JSONResponse:
        logger.error("request.failed_unexpected type=%s", type(exc).__name__)
        return _error_response(500, "internal_error", "An internal error occurred.")
