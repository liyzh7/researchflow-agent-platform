"""Authenticated owner-only audit/model receipts; traces are local-admin-only."""

import os
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select

from researchflow.auth.context import AuthError, Principal
from researchflow.auth.dependencies import require_identity
from researchflow.auth.models import AuditEvent
from researchflow.observability.models import ModelCall

router = APIRouter(tags=["observability"])
Identity = Annotated[Principal, Depends(require_identity)]


@router.get("/audit/events")
async def audit_events(principal: Identity, request: Request, limit: int = Query(20, ge=1, le=100)):
    statement = (
        select(AuditEvent).order_by(AuditEvent.occurred_at.desc(), AuditEvent.event_id).limit(limit)
    )
    if not principal.is_admin:
        statement = statement.where(AuditEvent.actor_id == principal.user_id)
    async with request.app.state.database.session() as session:
        rows = (await session.scalars(statement)).all()
    return {
        "items": [
            {
                "event_id": row.event_id,
                "actor_id": row.actor_id,
                "event_type": row.event_type,
                "target_type": row.target_type,
                "target_id": row.target_id,
                "outcome": row.outcome,
                "request_id": row.request_id,
                "trace_id": row.trace_id,
                "workflow_id": row.workflow_id,
                "occurred_at": row.occurred_at,
                "safe_metadata": row.safe_metadata,
            }
            for row in rows
        ]
    }


@router.get("/observability/model-calls")
async def model_calls(principal: Identity, request: Request, limit: int = Query(20, ge=1, le=100)):
    async with request.app.state.database.session() as session:
        rows = (
            await session.scalars(
                select(ModelCall)
                .where(ModelCall.actor_id == principal.user_id)
                .order_by(ModelCall.started_at.desc())
                .limit(limit)
            )
        ).all()
    return {
        "items": [
            {column.name: getattr(row, column.name) for column in ModelCall.__table__.columns}
            for row in rows
        ]
    }


@router.get("/observability/traces")
async def traces(principal: Identity, request: Request):
    if not principal.is_admin:
        raise AuthError("administrator_required", 403)
    spans = request.app.state.telemetry.exporter.get_finished_spans()
    return {
        "items": [
            {
                "name": span.name,
                "trace_id": f"{span.context.trace_id:032x}",
                "span_id": f"{span.context.span_id:016x}",
                "parent_id": None if span.parent is None else f"{span.parent.span_id:016x}",
                "attributes": dict(span.attributes),
                "status": span.status.status_code.name,
                "duration_ms": (span.end_time - span.start_time) / 1_000_000,
            }
            for span in spans
        ]
    }


@router.get("/observability/metrics")
async def metrics(principal: Identity, request: Request):
    if not principal.is_admin:
        raise AuthError("administrator_required", 403)
    provider = request.app.state.llm_provider
    metrics_data = request.app.state.telemetry.metric_reader.get_metrics_data()
    instruments = []
    if metrics_data is not None:
        for resource in metrics_data.resource_metrics:
            for scope in resource.scope_metrics:
                for metric in scope.metrics:
                    points = []
                    for point in metric.data.data_points:
                        item = {"attributes": dict(point.attributes)}
                        for field in ("value", "count", "sum"):
                            if hasattr(point, field):
                                item[field] = getattr(point, field)
                        points.append(item)
                    instruments.append({"name": metric.name, "unit": metric.unit, "points": points})
    return {
        "api_process_id": os.getpid(),
        "active": provider.active,
        "in_system": provider._in_system,
        "peak_active": provider.peak_active,
        "max_concurrency": provider.settings.llm_max_concurrency,
        "max_queue": provider.settings.llm_max_queue,
        "instruments": instruments,
    }
