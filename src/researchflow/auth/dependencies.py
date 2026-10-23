"""Authentication runs before any protected service/query or checkpoint access."""

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from researchflow.auth.audit import emit_audit
from researchflow.auth.context import AuthError, actor_scope
from researchflow.observability.tracing import operation

bearer = HTTPBearer(auto_error=False)


async def require_identity(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
):
    try:
        with operation("auth.authenticate"):
            principal = await request.app.state.auth_service.authenticate(
                None if credentials is None else credentials.credentials
            )
    except AuthError:
        await emit_audit(
            request.app.state.database,
            "permission.denied",
            "api",
            outcome="denied",
            reason="authentication_required",
        )
        raise
    request.state.actor_id = principal.user_id
    with actor_scope(principal):
        yield principal
