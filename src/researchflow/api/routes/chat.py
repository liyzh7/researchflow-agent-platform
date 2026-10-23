"""Provider-backed plain chat endpoint retained for backwards compatibility."""

from typing import Annotated

from fastapi import APIRouter, Depends

from researchflow.api.dependencies import get_chat_service
from researchflow.api.schemas import ChatRequest, ChatResponse
from researchflow.services.chat import ChatService

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> ChatResponse:
    result = await service.answer(body.question)
    return ChatResponse(
        answer=result.content,
        provider_mode="mock" if result.is_mock else "openai_compatible",
        provider_model=result.model,
        is_mock=result.is_mock,
        citations=[],
    )
