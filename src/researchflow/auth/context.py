"""Server-only identity context, inherited by awaited tools and graph nodes."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID


class AuthError(Exception):
    def __init__(self, code="authentication_required", status_code=401):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: UUID
    session_id: UUID | None = None
    is_admin: bool = False


_principal: ContextVar[Principal | None] = ContextVar("researchflow_principal", default=None)
request_id: ContextVar[str | None] = ContextVar("researchflow_request_id", default=None)
trace_id: ContextVar[str | None] = ContextVar("researchflow_trace_id", default=None)
workflow_id: ContextVar[UUID | None] = ContextVar("researchflow_workflow_id", default=None)


def current_principal() -> Principal:
    value = _principal.get()
    if value is None:
        raise AuthError()
    return value


def owner_id() -> UUID:
    return current_principal().user_id


def optional_actor() -> UUID | None:
    value = _principal.get()
    return None if value is None else value.user_id


@contextmanager
def actor_scope(principal: Principal | None):
    token = _principal.set(principal)
    try:
        yield
    finally:
        _principal.reset(token)
