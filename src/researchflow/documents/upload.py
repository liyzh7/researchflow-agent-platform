"""Bounded streaming upload reads and content hashing."""

from hashlib import sha256
from pathlib import PurePosixPath

from fastapi import UploadFile

from researchflow.documents.errors import (
    DocumentTooLargeError,
    InvalidDocumentError,
    UnsupportedDocumentTypeError,
)
from researchflow.documents.types import UploadedDocument

SUPPORTED_CONTENT_TYPES = {
    ".txt": {"application/octet-stream", "text/plain"},
    ".md": {
        "application/octet-stream",
        "text/markdown",
        "text/plain",
        "text/x-markdown",
    },
}


async def read_upload(
    upload: UploadFile,
    *,
    max_bytes: int,
    read_size: int,
) -> UploadedDocument:
    """Read in bounded chunks, enforcing the real body size and hashing bytes."""

    supplied_name = (upload.filename or "").replace("\\", "/")
    filename = PurePosixPath(supplied_name).name
    extension = PurePosixPath(filename).suffix.lower()
    if not filename or extension not in SUPPORTED_CONTENT_TYPES:
        raise UnsupportedDocumentTypeError

    content_type = (upload.content_type or "application/octet-stream").lower()
    content_type = content_type.split(";", maxsplit=1)[0].strip()
    if content_type not in SUPPORTED_CONTENT_TYPES[extension]:
        raise UnsupportedDocumentTypeError

    digest = sha256()
    parts: list[bytes] = []
    total = 0
    while data := await upload.read(read_size):
        total += len(data)
        if total > max_bytes:
            raise DocumentTooLargeError
        digest.update(data)
        parts.append(data)

    if total == 0:
        raise InvalidDocumentError

    return UploadedDocument(
        filename=filename[:255],
        content_type=content_type,
        raw_content=b"".join(parts),
        sha256=digest.hexdigest(),
        size=total,
        extension=extension,
    )
