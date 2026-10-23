"""Pure unit tests for upload validation, parsing, and chunk offsets."""

from hashlib import sha256
from io import BytesIO

import pytest
from starlette.datastructures import Headers, UploadFile

from researchflow.documents.chunker import CharacterChunker
from researchflow.documents.errors import (
    DocumentTooLargeError,
    InvalidDocumentError,
    UnsupportedDocumentTypeError,
)
from researchflow.documents.parsers import ParserRegistry
from researchflow.documents.types import UploadedDocument
from researchflow.documents.upload import read_upload


def uploaded(raw: bytes, extension: str = ".txt") -> UploadedDocument:
    return UploadedDocument(
        filename=f"paper{extension}",
        content_type="text/plain",
        raw_content=raw,
        sha256=sha256(raw).hexdigest(),
        size=len(raw),
        extension=extension,
    )


def test_parser_normalizes_newlines_and_preserves_unicode() -> None:
    result = ParserRegistry().parse(uploaded("格一行\r\nTab row\r格三行".encode()))

    assert result.text == "格一行\nTab row\n格三行"
    assert result.parser_version == "plain-text-v1"


def test_markdown_parser_preserves_source_and_blank_lines() -> None:
    result = ParserRegistry().parse(uploaded("# Cards\n\n登记".encode(), ".md"))

    assert result.text == "# Cards\n\n登记"
    assert result.parser_version == "markdown-source-v1"


@pytest.mark.parametrize("raw", [b"", b" \n\t ", b"abc\x00def", b"\xff\xfe"])
def test_parser_rejects_empty_binary_or_invalid_utf8(raw: bytes) -> None:
    with pytest.raises(InvalidDocumentError):
        ParserRegistry().parse(uploaded(raw))


def test_parser_rejects_unknown_extension() -> None:
    with pytest.raises(UnsupportedDocumentTypeError):
        ParserRegistry().parse(uploaded(b"data", ".pdf"))


def test_chunker_is_deterministic_with_exact_offsets_and_lines() -> None:
    text = "南北\nxyz\n终章"
    chunker = CharacterChunker(chunk_size=5, overlap=2)

    first = chunker.split(text)
    second = chunker.split(text)

    assert first == second
    assert [(c.char_start, c.char_end) for c in first] == [(0, 5), (3, 8), (6, 9)]
    assert [c.content for c in first] == [text[0:5], text[3:8], text[6:9]]
    assert [(c.line_start, c.line_end) for c in first] == [(1, 2), (2, 3), (2, 3)]


def test_chunker_handles_short_and_long_text() -> None:
    chunker = CharacterChunker(chunk_size=100, overlap=10)
    assert len(chunker.split("short")) == 1

    chunks = chunker.split("x" * 250)
    assert len(chunks) == 3
    assert chunks[-1].char_end == 250
    assert all(chunk.content for chunk in chunks)


@pytest.mark.anyio
async def test_upload_streams_and_hashes_actual_bytes() -> None:
    raw = "格子 ledger".encode()
    file = UploadFile(
        BytesIO(raw),
        filename="..\\safe.txt",
        headers=Headers({"content-type": "text/plain"}),
    )

    result = await read_upload(file, max_bytes=100, read_size=3)

    assert result.filename == "safe.txt"
    assert result.raw_content == raw
    assert result.sha256 == sha256(raw).hexdigest()


@pytest.mark.anyio
async def test_upload_accepts_exact_size_limit() -> None:
    file = UploadFile(
        BytesIO(b"12345"),
        filename="boundary.txt",
        headers=Headers({"content-type": "text/plain"}),
    )

    result = await read_upload(file, max_bytes=5, read_size=2)

    assert result.size == 5


@pytest.mark.anyio
async def test_upload_enforces_real_stream_size() -> None:
    file = UploadFile(
        BytesIO(b"123456"),
        filename="large.md",
        headers=Headers({"content-type": "text/markdown"}),
    )

    with pytest.raises(DocumentTooLargeError):
        await read_upload(file, max_bytes=5, read_size=2)


@pytest.mark.anyio
async def test_upload_rejects_mime_extension_mismatch() -> None:
    file = UploadFile(
        BytesIO(b"hello"),
        filename="paper.txt",
        headers=Headers({"content-type": "application/pdf"}),
    )

    with pytest.raises(UnsupportedDocumentTypeError):
        await read_upload(file, max_bytes=100, read_size=10)
