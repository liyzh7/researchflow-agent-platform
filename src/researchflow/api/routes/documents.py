"""Versioned research document ingestion and inspection endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile

from researchflow.api.dependencies import (
    get_document_service,
    get_indexing_service,
    get_settings_from_app,
)
from researchflow.api.schemas import (
    ChunkResponse,
    DocumentPageResponse,
    DocumentResponse,
    IngestionResponse,
    VersionResponse,
)
from researchflow.core.config import Settings
from researchflow.documents.service import DocumentService
from researchflow.documents.upload import read_upload
from researchflow.indexing.service import IndexingService

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("/{document_id}/versions/{version_id}/index")
async def document_index_status(
    document_id: UUID,
    version_id: UUID,
    documents: Annotated[DocumentService, Depends(get_document_service)],
    indexing: Annotated[IndexingService, Depends(get_indexing_service)],
):
    await documents.list_chunks(document_id, version_id)
    return await indexing.status(version_id)


@router.post("/{document_id}/versions/{version_id}/index")
async def index_document_version(
    document_id: UUID,
    version_id: UUID,
    documents: Annotated[DocumentService, Depends(get_document_service)],
    indexing: Annotated[IndexingService, Depends(get_indexing_service)],
):
    await documents.list_chunks(document_id, version_id)
    return await indexing.index_version(version_id)


@router.post("", response_model=IngestionResponse, status_code=201)
async def ingest_document(
    response: Response,
    document_key: Annotated[
        str,
        Form(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"),
    ],
    title: Annotated[str, Form(min_length=1, max_length=300, pattern=r".*\S.*")],
    file: Annotated[UploadFile, File()],
    settings: Annotated[Settings, Depends(get_settings_from_app)],
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> IngestionResponse:
    try:
        uploaded = await read_upload(
            file,
            max_bytes=settings.upload_max_bytes,
            read_size=settings.upload_read_size,
        )
        result = await service.ingest(
            document_key=document_key,
            title=title.strip(),
            uploaded=uploaded,
        )
    finally:
        await file.close()

    if result.is_duplicate:
        response.status_code = 200
    return IngestionResponse.model_validate(result, from_attributes=True)


@router.get("", response_model=DocumentPageResponse)
async def list_documents(
    service: Annotated[DocumentService, Depends(get_document_service)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> DocumentPageResponse:
    page = await service.list_documents(offset=offset, limit=limit)
    return DocumentPageResponse(
        items=[DocumentResponse.model_validate(item, from_attributes=True) for item in page.items],
        total=page.total,
        offset=page.offset,
        limit=page.limit,
    )


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: UUID,
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> DocumentResponse:
    result = await service.get_document(document_id)
    return DocumentResponse.model_validate(result, from_attributes=True)


@router.get("/{document_id}/versions", response_model=list[VersionResponse])
async def list_document_versions(
    document_id: UUID,
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> list[VersionResponse]:
    versions = await service.list_versions(document_id)
    return [VersionResponse.model_validate(item, from_attributes=True) for item in versions]


@router.get(
    "/{document_id}/versions/{version_id}/chunks",
    response_model=list[ChunkResponse],
)
async def list_version_chunks(
    document_id: UUID,
    version_id: UUID,
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> list[ChunkResponse]:
    chunks = await service.list_chunks(document_id, version_id)
    return [ChunkResponse.model_validate(item, from_attributes=True) for item in chunks]
