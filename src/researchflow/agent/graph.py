"""Actual LangGraph execution: references in state, business receipts in PostgreSQL."""

from functools import wraps
from typing import Literal, TypedDict
from uuid import UUID

from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy.exc import SQLAlchemyError

from researchflow.agent.errors import AgentError
from researchflow.agent.repository import AgentRepository
from researchflow.agent.schemas import DraftContent
from researchflow.agent.tools import AgentTools
from researchflow.observability.tracing import operation
from researchflow.providers.errors import LLMProviderError
from researchflow.rag.errors import RAGError


class AgentState(TypedDict, total=False):
    run_id: str
    intent: str
    route_mode: str
    tool_calls: int
    proposal_id: str


def build_graph(repository: AgentRepository, tools: AgentTools, checkpointer):
    async def validate_request(state: AgentState):
        await repository.get_run(UUID(state["run_id"]))
        return {"tool_calls": state.get("tool_calls", 0)}

    async def route_intent(state: AgentState):
        run = await repository.get_run(UUID(state["run_id"]))
        intent = run.request_data["intent"]
        mode = "explicit_request"
        telemetry = None
        if intent == "auto":
            await repository.consume_tool(run.id)
            intent, telemetry = await tools.classify(run.request_data["query"])
            mode = "model_structured_intent"
        result = dict(run.result or {})
        result.update({"route": intent, "route_mode": mode, "routing_telemetry": telemetry})
        await repository.update_run(run.id, result=result)
        return {"intent": intent, "route_mode": mode}

    async def consume(state: AgentState):
        return await repository.consume_tool(UUID(state["run_id"]))

    async def retrieve_evidence(state: AgentState):
        count = await consume(state)
        run = await repository.get_run(UUID(state["run_id"]))
        result = dict(run.result or {})
        if "knowledge" not in result:
            result["knowledge"] = await tools.invoke(
                "answer_with_citations",
                {
                    "query": run.request_data["query"],
                    "document_key": run.request_data.get("document_key"),
                },
            )
            await repository.update_run(run.id, result=result)
        return {"tool_calls": count}

    async def answer_question(state: AgentState):
        await repository.update_run(UUID(state["run_id"]), status="COMPLETED", error_code=None)
        return {}

    async def list_tasks(state: AgentState):
        count = await consume(state)
        run = await repository.get_run(UUID(state["run_id"]))
        result = dict(run.result or {})
        result["tasks"] = await tools.invoke("list_research_tasks", {})
        await repository.update_run(run.id, status="COMPLETED", result=result, error_code=None)
        return {"tool_calls": count}

    async def generate_task_draft(state: AgentState):
        count = await consume(state)
        run = await repository.get_run(UUID(state["run_id"]))
        existing = await repository.latest_proposal(run.id)
        if existing is None:
            knowledge = run.result["knowledge"]
            if knowledge["status"] != "answered":
                raise AgentError("insufficient_task_evidence", 422)
            generated = await tools.invoke(
                "prepare_task_draft",
                {"query": run.request_data["query"], "evidence": knowledge["citations"]},
            )
            existing = await repository.save_proposal(
                run.id, DraftContent.model_validate(generated["content"]), knowledge["citations"]
            )
            result = dict(run.result)
            result["draft_telemetry"] = generated["telemetry"]
            await repository.update_run(run.id, result=result)
        return {"tool_calls": count, "proposal_id": str(existing.id)}

    async def validate_task_draft(state: AgentState):
        latest = await repository.latest_proposal(UUID(state["run_id"]))
        await repository.update_run(
            UUID(state["run_id"]), status="AWAITING_APPROVAL", error_code=None
        )
        return {"proposal_id": str(latest.id)}

    async def request_human_approval(
        state: AgentState,
    ) -> Command[Literal["persist_approved_tasks", "validate_task_draft", "reject"]]:
        # No writes or model calls before interrupt: this prefix runs again on resume.
        proposal = await repository.proposal(UUID(state["proposal_id"]))
        decision_id = interrupt(
            {
                "proposal_id": str(proposal.id),
                "proposal_version": proposal.proposal_version,
                "content_digest": proposal.content_digest,
                "content": proposal.structured_content,
                "evidence": proposal.evidence,
            }
        )
        approval = await repository.approval_for(proposal.id)
        if approval is None or str(approval.id) != decision_id:
            raise AgentError("untrusted_resume_decision", 403)
        destination = {
            "APPROVE": "persist_approved_tasks",
            "REJECT": "reject",
            "EDIT_AND_REVIEW": "validate_task_draft",
        }[approval.decision]
        return Command(goto=destination)

    async def persist_approved_tasks(state: AgentState):
        if repository.fault_hook is not None:
            repository.fault_hook("before_task_write")
        await repository.persist(UUID(state["run_id"]), UUID(state["proposal_id"]))
        return {}

    async def reject(state: AgentState):
        await repository.update_run(UUID(state["run_id"]), status="REJECTED", error_code=None)
        return {}

    builder = StateGraph(AgentState)
    nodes = {
        "validate_request": validate_request,
        "route_intent": route_intent,
        "retrieve_evidence": retrieve_evidence,
        "answer_question": answer_question,
        "list_tasks": list_tasks,
        "generate_task_draft": generate_task_draft,
        "validate_task_draft": validate_task_draft,
        "request_human_approval": request_human_approval,
        "persist_approved_tasks": persist_approved_tasks,
        "reject": reject,
    }

    def guarded(node):
        @wraps(node)
        async def invoke(state):
            try:
                with operation("agent.node." + node.__name__):
                    return await node(state)
            except (AgentError, GraphBubbleUp):
                raise
            except LLMProviderError as exc:
                raise AgentError(f"agent_provider_{exc.category.value}", 503) from None
            except RAGError as exc:
                raise AgentError(exc.code, 503) from None
            except TimeoutError:
                raise AgentError("agent_tool_timeout", 503) from None
            except SQLAlchemyError:
                raise AgentError("agent_database_unavailable", 503) from None
            except Exception:
                # Saver persists failed-task errors: sanitize BEFORE LangGraph receives them.
                # GraphBubbleUp/GraphInterrupt are control flow, preserved above.
                raise AgentError("agent_node_failed", 503) from None

        return invoke

    for name, node in nodes.items():
        builder.add_node(name, guarded(node))
    builder.add_edge(START, "validate_request")
    builder.add_edge("validate_request", "route_intent")
    builder.add_conditional_edges(
        "route_intent",
        lambda state: state["intent"],
        {"question": "retrieve_evidence", "tasks": "retrieve_evidence", "list_tasks": "list_tasks"},
    )
    builder.add_conditional_edges(
        "retrieve_evidence",
        lambda state: state["intent"],
        {"question": "answer_question", "tasks": "generate_task_draft"},
    )
    builder.add_edge("generate_task_draft", "validate_task_draft")
    builder.add_edge("validate_task_draft", "request_human_approval")
    for name in ("answer_question", "list_tasks", "persist_approved_tasks", "reject"):
        builder.add_edge(name, END)
    return builder.compile(checkpointer=checkpointer, name="researchflow_agent_v1")
