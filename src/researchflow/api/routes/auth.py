"""No anonymous signup: users are provisioned from the local getpass-based CLI."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from researchflow.auth.context import Principal
from researchflow.auth.dependencies import require_identity
from researchflow.auth.models import AppUser

router = APIRouter(prefix="/auth", tags=["auth"])
Identity = Annotated[Principal, Depends(require_identity)]


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: SecretStr = Field(min_length=1, max_length=256)


@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await request.app.state.auth_service.login(
        body.username,
        body.password.get_secret_value(),
        "unknown" if request.client is None else request.client.host,
    )


@router.post("/logout", status_code=204)
async def logout(principal: Identity, request: Request):
    await request.app.state.auth_service.revoke(principal, principal.session_id)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.get("/me")
async def me(principal: Identity, request: Request):
    async with request.app.state.database.session() as session:
        user = await session.get(AppUser, principal.user_id)
    return {
        "id": str(user.id),
        "username": user.username,
        "is_active": user.is_active,
        "is_admin": user.is_admin,
    }


@router.post("/sessions/{session_id}/revoke", status_code=204)
async def revoke(session_id: UUID, principal: Identity, request: Request):
    await request.app.state.auth_service.revoke(principal, session_id)
    return Response(status_code=204)
