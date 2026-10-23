"""Top-level router composition."""

from fastapi import APIRouter, Depends

from researchflow.api.routes import (
    agent,
    auth,
    chat,
    documents,
    observability,
    rag,
    retrieval,
    system,
)
from researchflow.auth.dependencies import require_identity

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(system.router)
api_router.include_router(auth.router)
api_router.include_router(observability.router)
for protected in (chat.router, documents.router, retrieval.router, rag.router, agent.router):
    api_router.include_router(protected, dependencies=[Depends(require_identity)])
