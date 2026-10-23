"""Index construction errors with stable machine-facing categories."""


class IndexingError(Exception):
    code = "indexing_failed"


class IndexingSourceNotFoundError(IndexingError):
    code = "indexing_source_not_found"


class IndexingAttemptsExhaustedError(IndexingError):
    code = "indexing_attempts_exhausted"


class IndexingSourceMismatchError(IndexingError):
    code = "indexing_source_mismatch"
