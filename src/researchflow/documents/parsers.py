"""Small parser abstraction for accepted plain-text document formats."""

from typing import Protocol

from researchflow.documents.errors import InvalidDocumentError, UnsupportedDocumentTypeError
from researchflow.documents.types import ParsedDocument, UploadedDocument


class DocumentParser(Protocol):
    version: str

    def parse(self, document: UploadedDocument) -> ParsedDocument: ...


def _decode_normalized_utf8(document: UploadedDocument, parser_version: str) -> ParsedDocument:
    try:
        text = document.raw_content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidDocumentError from exc

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.strip():
        raise InvalidDocumentError
    if any((ord(char) < 32 and char not in "\t\n") or ord(char) == 127 for char in normalized):
        raise InvalidDocumentError
    return ParsedDocument(text=normalized, parser_version=parser_version)


class PlainTextParser:
    version = "plain-text-v1"

    def parse(self, document: UploadedDocument) -> ParsedDocument:
        return _decode_normalized_utf8(document, self.version)


class MarkdownParser:
    version = "markdown-source-v1"

    def parse(self, document: UploadedDocument) -> ParsedDocument:
        # Stage 2 preserves Markdown source text. Structural parsing belongs to a
        # later retrieval-focused stage and must not silently rewrite offsets.
        return _decode_normalized_utf8(document, self.version)


class ParserRegistry:
    def __init__(self) -> None:
        self._parsers: dict[str, DocumentParser] = {
            ".txt": PlainTextParser(),
            ".md": MarkdownParser(),
        }

    def parse(self, document: UploadedDocument) -> ParsedDocument:
        parser = self._parsers.get(document.extension)
        if parser is None:
            raise UnsupportedDocumentTypeError
        return parser.parse(document)
