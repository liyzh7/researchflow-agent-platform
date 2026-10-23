"""Classified document ingestion failures safe to map to HTTP responses."""


class DocumentError(Exception):
    code = "document_error"
    status_code = 400
    public_message = "The document could not be processed."


class UnsupportedDocumentTypeError(DocumentError):
    code = "unsupported_document_type"
    status_code = 415
    public_message = "Only UTF-8 .txt and .md documents are supported."


class InvalidDocumentError(DocumentError):
    code = "invalid_document"
    public_message = "The document is empty, binary, or not valid UTF-8 text."


class DocumentTooLargeError(DocumentError):
    code = "document_too_large"
    status_code = 413
    public_message = "The document exceeds the configured upload size limit."


class DocumentNotFoundError(DocumentError):
    code = "document_not_found"
    status_code = 404
    public_message = "The requested document or version does not exist."


class DatabaseUnavailableError(DocumentError):
    code = "database_unavailable"
    status_code = 503
    public_message = "The document database is unavailable."


class DocumentIngestionError(DocumentError):
    code = "document_ingestion_failed"
    status_code = 500
    public_message = "The document import transaction failed."
