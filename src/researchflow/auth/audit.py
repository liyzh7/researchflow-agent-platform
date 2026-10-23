"""Allowlisted metadata only. Successful writes append audit in the SAME transaction."""

import re
from contextvars import ContextVar

from opentelemetry import trace

from researchflow.auth.context import optional_actor, request_id, workflow_id
from researchflow.auth.models import AuditEvent

_KEYS = frozenset(
    {"count", "status", "failure_category", "decision", "version", "reason", "source", "owner_id"}
)
_VALUE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
audit_writer: ContextVar[object | None] = ContextVar("researchflow_audit_writer", default=None)


def add_audit(session, event_type, target_type, target_id=None, outcome="success", **metadata):
    actor = metadata.pop("actor_id", optional_actor())
    workflow = metadata.pop("workflow", workflow_id.get())
    safe = {}
    for key, value in metadata.items():
        if key not in _KEYS:
            raise ValueError("audit metadata key is not allowlisted")
        if not isinstance(value, (bool, int, float)) and not (
            isinstance(value, str) and _VALUE.fullmatch(value)
        ):
            raise ValueError("audit metadata value is not safe")
        safe[key] = value
    context = trace.get_current_span().get_span_context()
    row = AuditEvent(
        actor_id=actor,
        event_type=event_type,
        target_type=target_type,
        target_id=None if target_id is None else str(target_id),
        outcome=outcome,
        request_id=request_id.get(),
        trace_id=f"{context.trace_id:032x}" if context.is_valid else None,
        workflow_id=workflow,
        safe_metadata=safe,
    )
    session.add(row)
    return row


async def emit_audit(database, *args, **kwargs):
    injected = audit_writer.get()
    if injected is not None:
        await injected(*args, **kwargs)
        return
    # Fail closed on audit-storage failure; never silently acknowledge business success.
    async with database.session() as session, session.begin():
        add_audit(session, *args, **kwargs)
