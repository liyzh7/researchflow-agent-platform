"""Evidence-only retrieval API; no answer generation or fabricated citations."""

from typing import Annotated

from fastapi import APIRouter, Depends

from researchflow.api.dependencies import get_retrieval_service
from researchflow.api.schemas import (
    RetrievalHitResponse,
    RetrievalSearchRequest,
    RetrievalSearchResponse,
)
from researchflow.retrieval.service import RetrievalService

router = APIRouter(prefix="/retrieval", tags=["retrieval"])


@router.post("/search", response_model=RetrievalSearchResponse)
async def search(
    request: RetrievalSearchRequest,
    service: Annotated[RetrievalService, Depends(get_retrieval_service)],
) -> RetrievalSearchResponse:
    result = await service.search(
        query=request.query,
        top_k=request.top_k,
        mode=request.mode,
        document_id=request.document_id,
        document_key=request.document_key,
    )
    return RetrievalSearchResponse(
        status=result.status,
        mode=result.mode,
        items=[
            RetrievalHitResponse.model_validate(item, from_attributes=True) for item in result.items
        ],
        model=result.model,
        rrf_k=result.rrf_k,
    )
