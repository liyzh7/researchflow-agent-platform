"""Local single-user workflow and internal task APIs."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from researchflow.agent.errors import AgentError
from researchflow.agent.schemas import DecisionRequest, RunRequest, TaskView
from researchflow.agent.service import AgentService
from researchflow.auth.audit import emit_audit

router = APIRouter(tags=["agent"])
WorkflowToken = Annotated[str | None, Header(alias="X-Workflow-Token", max_length=128)]


def get_agent_service(request: Request) -> AgentService:
    if not request.app.state.settings.agent_enabled:
        raise AgentError("agent_disabled", 503)
    return request.app.state.agent_service


Service = Annotated[AgentService, Depends(get_agent_service)]


@router.get("/agent/runs")
async def list_runs(
    service: Service, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)
):
    rows, total = await service.repository.list_runs(offset=offset, limit=limit)
    return {
        "items": [
            {
                "run_id": str(row.id),
                "status": row.status,
                "query": row.request_data["query"],
                "updated_at": row.updated_at,
                "expires_at": row.expires_at,
            }
            for row in rows
        ],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@router.post("/agent/runs/{run_id}/access")
async def recover_owned_access(run_id: UUID, service: Service, response: Response):
    # Re-authenticated owner only. No client-selected thread or persisted browser capability.
    await service.repository.get_run(run_id)
    response.headers["Cache-Control"] = "no-store"
    await emit_audit(
        service.repository.database,
        "agent.access_recovered",
        "workflow",
        run_id,
        workflow=run_id,
    )
    return {"workflow_token": service.repository.token(run_id)}


@router.post("/agent/runs", status_code=201)
async def create_run(
    body: RunRequest,
    service: Service,
    x_workflow_token: WorkflowToken = None,
    idempotency_key: Annotated[str, Header(min_length=16, max_length=128)] = "",
):
    if not 16 <= len(idempotency_key) <= 128:
        raise AgentError("idempotency_key_required", 422)
    return await service.create(body, idempotency_key, x_workflow_token)


@router.get("/agent/runs/{run_id}")
async def get_run(run_id: UUID, service: Service, x_workflow_token: WorkflowToken = None):
    return await service.view(run_id, x_workflow_token)


@router.post("/agent/runs/{run_id}/decisions")
async def decide(
    run_id: UUID, body: DecisionRequest, service: Service, x_workflow_token: WorkflowToken = None
):
    return await service.decide(run_id, body, x_workflow_token)


@router.post("/agent/runs/{run_id}/resume")
async def resume(run_id: UUID, service: Service, x_workflow_token: WorkflowToken = None):
    return await service.resume(run_id, x_workflow_token)


@router.get("/tasks")
async def tasks(
    service: Service, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)
):
    rows, total = await service.repository.list_tasks(offset=offset, limit=limit)
    return {
        "items": [TaskView.model_validate(row, from_attributes=True) for row in rows],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@router.get("/tasks/{task_id}", response_model=TaskView)
async def task(task_id: UUID, service: Service):
    row = await service.repository.get_task(task_id)
    return TaskView.model_validate(row, from_attributes=True)
