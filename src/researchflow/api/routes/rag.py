"""Evidence-gated RAG answer endpoint."""

from typing import Annotated

from fastapi import APIRouter, Depends

from researchflow.api.dependencies import get_rag_service
from researchflow.api.schemas import RAGAnswerRequest, RAGAnswerResponse
from researchflow.rag.service import RAGService

router = APIRouter(prefix="/rag", tags=["rag"])


@router.post("/answer", response_model=RAGAnswerResponse)
async def answer_with_evidence(
    body: RAGAnswerRequest,
    service: Annotated[RAGService, Depends(get_rag_service)],
) -> RAGAnswerResponse:
    result = await service.answer(
        query=body.query,
        retrieval_mode=body.retrieval_mode,
        top_k=body.top_k,
        rerank_enabled=body.rerank_enabled,
        document_id=body.document_id,
        document_key=body.document_key,
    )
    return RAGAnswerResponse.model_validate(result, from_attributes=True)
