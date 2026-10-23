"""Health and service information endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends

from researchflow.api.dependencies import get_settings_from_app
from researchflow.api.schemas import InfoResponse
from researchflow.core.config import Settings

router = APIRouter(tags=["system"])


@router.get("/info", response_model=InfoResponse)
async def get_info(
    settings: Annotated[Settings, Depends(get_settings_from_app)],
) -> InfoResponse:
    return InfoResponse(
        project_name=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
        provider_mode=settings.llm_provider_mode,
        rag_enabled=settings.rag_enabled,
        agent_enabled=settings.agent_enabled,
        upload_max_bytes=settings.upload_max_bytes,
    )
