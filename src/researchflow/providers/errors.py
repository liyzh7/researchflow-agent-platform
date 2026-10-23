"""Provider error categories shared by future model integrations."""

from enum import StrEnum


class LLMErrorCategory(StrEnum):
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    INVALID_REQUEST = "invalid_request"
    INVALID_RESPONSE = "invalid_response"
    AUTHENTICATION = "authentication"
    RATE_LIMITED = "rate_limited"
    UPSTREAM = "upstream"


class LLMProviderError(Exception):
    """Base error whose category can be mapped to a stable API response."""

    def __init__(self, message: str, category: LLMErrorCategory) -> None:
        super().__init__(message)
        self.category = category


class LLMProviderTimeoutError(LLMProviderError):
    def __init__(self, message: str = "provider request timed out") -> None:
        super().__init__(message, LLMErrorCategory.TIMEOUT)


class LLMProviderUnavailableError(LLMProviderError):
    def __init__(self, message: str = "provider is unavailable") -> None:
        super().__init__(message, LLMErrorCategory.UNAVAILABLE)


class LLMProviderBusyError(LLMProviderUnavailableError):
    """Local admission rejection, distinct from an offline model server."""


class LLMProviderInvalidRequestError(LLMProviderError):
    def __init__(self, message: str = "provider request is invalid") -> None:
        super().__init__(message, LLMErrorCategory.INVALID_REQUEST)


class LLMProviderInvalidResponseError(LLMProviderError):
    def __init__(self, message: str = "provider response is invalid") -> None:
        super().__init__(message, LLMErrorCategory.INVALID_RESPONSE)


class LLMProviderRedirectError(LLMProviderInvalidResponseError):
    def __init__(self) -> None:
        super().__init__("provider redirect blocked; configure the final endpoint URL")


class LLMProviderAuthenticationError(LLMProviderError):
    def __init__(self, message: str = "provider credentials are unavailable or rejected") -> None:
        super().__init__(message, LLMErrorCategory.AUTHENTICATION)


class LLMProviderRateLimitError(LLMProviderError):
    def __init__(self, message: str = "provider rate limit exceeded") -> None:
        super().__init__(message, LLMErrorCategory.RATE_LIMITED)


class LLMProviderUpstreamError(LLMProviderError):
    def __init__(self, message: str = "provider returned an upstream error") -> None:
        super().__init__(message, LLMErrorCategory.UPSTREAM)


class RerankerErrorCategory(StrEnum):
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    INVALID_INPUT = "invalid_input"
    INVALID_RESPONSE = "invalid_response"


class RerankerProviderError(Exception):
    def __init__(self, message: str, category: RerankerErrorCategory) -> None:
        super().__init__(message)
        self.category = category


class RerankerProviderTimeoutError(RerankerProviderError):
    def __init__(self, message: str = "reranker operation timed out") -> None:
        super().__init__(message, RerankerErrorCategory.TIMEOUT)


class RerankerProviderUnavailableError(RerankerProviderError):
    def __init__(self, message: str = "reranker provider is unavailable") -> None:
        super().__init__(message, RerankerErrorCategory.UNAVAILABLE)


class RerankerProviderInvalidInputError(RerankerProviderError):
    def __init__(self, message: str = "reranker input is invalid") -> None:
        super().__init__(message, RerankerErrorCategory.INVALID_INPUT)


class RerankerProviderInvalidResponseError(RerankerProviderError):
    def __init__(self, message: str = "reranker output is invalid") -> None:
        super().__init__(message, RerankerErrorCategory.INVALID_RESPONSE)


class EmbeddingErrorCategory(StrEnum):
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    INVALID_INPUT = "invalid_input"
    INVALID_VECTOR = "invalid_vector"


class EmbeddingProviderError(Exception):
    """Stable embedding failure category without exposing model internals."""

    def __init__(self, message: str, category: EmbeddingErrorCategory) -> None:
        super().__init__(message)
        self.category = category


class EmbeddingProviderTimeoutError(EmbeddingProviderError):
    def __init__(self, message: str = "embedding operation timed out") -> None:
        super().__init__(message, EmbeddingErrorCategory.TIMEOUT)


class EmbeddingProviderUnavailableError(EmbeddingProviderError):
    def __init__(self, message: str = "embedding provider is unavailable") -> None:
        super().__init__(message, EmbeddingErrorCategory.UNAVAILABLE)


class EmbeddingProviderInvalidInputError(EmbeddingProviderError):
    def __init__(self, message: str = "embedding input is invalid") -> None:
        super().__init__(message, EmbeddingErrorCategory.INVALID_INPUT)


class EmbeddingProviderInvalidVectorError(EmbeddingProviderError):
    def __init__(self, message: str = "embedding output is invalid") -> None:
        super().__init__(message, EmbeddingErrorCategory.INVALID_VECTOR)
