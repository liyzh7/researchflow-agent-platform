"""Application-level authorization, graph driving and committed-result recovery."""

from uuid import UUID

from langgraph.types import Command
from psycopg import Error as PsycopgError
from psycopg_pool import PoolTimeout
from sqlalchemy.exc import SQLAlchemyError

from researchflow.agent.errors import AgentError
from researchflow.agent.graph import build_graph
from researchflow.agent.repository import AgentRepository
from researchflow.agent.schemas import DecisionRequest, RunRequest
from researchflow.agent.tools import AgentTools
from researchflow.auth.audit import emit_audit
from researchflow.auth.context import workflow_id
from researchflow.observability.tracing import operation
from researchflow.providers.errors import LLMProviderError
from researchflow.rag.errors import RAGError


class AgentService:
    def __init__(self, repository: AgentRepository, tools: AgentTools, checkpointer) -> None:
        self.repository = repository
        self.tools = tools
        self.graph = build_graph(repository, tools, checkpointer)

    async def create(self, body: RunRequest, key: str, token: str | None):
        run, created = await self.repository.create(body, key, token)
        async with self.repository.execution_lock(run.id):
            try:
                await self._advance(run.id)
            except AgentError:
                # Creation always returns the capability even when execution fails;
                # the durable FAILED record can then be inspected/resumed explicitly.
                pass
            result = await self.view(run.id, self.repository.token(run.id))
            result["workflow_token"] = self.repository.token(run.id)
            result["created"] = created
            return result

    async def view(self, run_id: UUID, token: str | None):
        run = await self.repository.authorize(run_id, token)
        proposal = await self.repository.latest_proposal(run_id)
        approval = None if proposal is None else await self.repository.approval_for(proposal.id)
        config = self._config(run.thread_id)
        checkpoint_status = "available"
        next_nodes = []
        try:
            snapshot = await self.graph.aget_state(config)
            next_nodes = list(snapshot.next)
        except Exception:
            checkpoint_status = "unavailable"
        return {
            "run_id": str(run.id),
            "status": run.status,
            "result": run.result,
            "error_code": run.error_code,
            "tool_calls": run.tool_calls,
            "expires_at": run.expires_at.isoformat(),
            "checkpoint_status": checkpoint_status,
            "next_nodes": next_nodes,
            "proposal": None
            if proposal is None
            else {
                "id": str(proposal.id),
                "version": proposal.proposal_version,
                "content_digest": proposal.content_digest,
                "status": proposal.status,
                "content": proposal.structured_content,
                "evidence": proposal.evidence,
            },
            "approval": None
            if approval is None
            else {
                "id": str(approval.id),
                "decision": approval.decision,
                "execution_status": approval.execution_status,
            },
        }

    async def decide(self, run_id: UUID, body: DecisionRequest, token: str | None):
        await self.repository.authorize(run_id, token)
        async with self.repository.execution_lock(run_id):
            await self.repository.decide(run_id, body)
            if self.repository.fault_hook is not None:
                self.repository.fault_hook("after_approval_commit")
            await self._advance(run_id)
            return await self.view(run_id, token)

    async def resume(self, run_id: UUID, token: str | None):
        await self.repository.authorize(run_id, token)
        async with self.repository.execution_lock(run_id):
            await self._advance(run_id)
            return await self.view(run_id, token)

    async def _advance(self, run_id: UUID):
        workflow_token = workflow_id.set(run_id)
        try:
            with operation("agent.execute"):
                return await self._drive(run_id)
        finally:
            workflow_id.reset(workflow_token)

    async def _drive(self, run_id: UUID):
        run = await self.repository.get_run(run_id)
        if run.status in {"COMPLETED", "REJECTED"}:
            return
        config = self._config(run.thread_id)
        try:
            snapshot = await self.graph.aget_state(config)
            if not snapshot.values:
                graph_input = {"run_id": str(run_id), "tool_calls": 0}
            else:
                graph_input = None
                if any(task.interrupts for task in snapshot.tasks):
                    proposal_id = UUID(snapshot.values["proposal_id"])
                    approval = await self.repository.approval_for(proposal_id)
                    if approval is None:
                        await self.repository.update_run(
                            run_id, status="AWAITING_APPROVAL", error_code=None
                        )
                        return
                    graph_input = Command(resume=str(approval.id))
            await self.graph.ainvoke(graph_input, config=config, durability="sync")
        except Exception as exc:
            # Never store repr/traceback of models, connections, prompts or API secrets.
            if isinstance(exc, AgentError):
                code = exc.code
            elif isinstance(exc, LLMProviderError):
                code = f"agent_provider_{exc.category.value}"
            elif isinstance(exc, RAGError):
                code = exc.code
            elif isinstance(exc, TimeoutError):
                code = "agent_tool_timeout"
            elif isinstance(exc, SQLAlchemyError):
                code = "agent_database_unavailable"
            elif isinstance(exc, (PsycopgError, PoolTimeout, OSError)):
                code = "checkpoint_unavailable"
            else:
                code = "agent_execution_unavailable"
            current = await self.repository.get_run(run_id)
            if current.status != "COMPLETED":
                await self.repository.update_run(run_id, status="FAILED", error_code=code)
                await emit_audit(
                    self.repository.database,
                    "task.write_failed" if run.status == "AWAITING_APPROVAL" else "agent.failed",
                    "workflow",
                    run_id,
                    outcome="failed",
                    workflow=run_id,
                    failure_category=code,
                )
            raise AgentError(code, 503) from None

    @staticmethod
    def _config(thread_id: str):
        return {"configurable": {"thread_id": thread_id}, "recursion_limit": 64, "callbacks": []}
