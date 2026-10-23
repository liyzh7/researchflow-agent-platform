"""RAG orchestration failures distinct from normal evidence refusal."""


class RAGError(Exception):
    status_code = 503
    code = "rag_unavailable"
    public_message = "Grounded answering is unavailable."


class RAGConfigurationError(RAGError):
    code = "rag_not_configured"
    public_message = "Grounded answering is not enabled."


class CitationValidationError(RAGError):
    status_code = 502
    code = "invalid_model_citation"
    public_message = "The model returned an invalid citation."


class CitationRepositoryUnavailableError(RAGError):
    code = "citation_source_unavailable"
    public_message = "Citation sources are temporarily unavailable."
