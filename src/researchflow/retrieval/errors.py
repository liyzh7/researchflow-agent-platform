"""Publicly safe retrieval failure categories."""


class RetrievalError(Exception):
    status_code = 503
    code = "retrieval_unavailable"
    public_message = "Retrieval is temporarily unavailable."


class RetrievalConfigurationError(RetrievalError):
    status_code = 500
    code = "retrieval_configuration_error"
    public_message = "Retrieval is not configured correctly."
