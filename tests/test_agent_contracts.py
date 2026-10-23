"""Network-free schema, authorization, tool and serialization boundaries."""

import json
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import ValidationError

from researchflow.agent.checkpoint import strict_serializer
from researchflow.agent.errors import AgentError
from researchflow.agent.schemas import DecisionRequest, DraftContent, IntentOutput, RunRequest
from researchflow.agent.tools import AgentTools


@pytest.mark.parametrize(
    "body",
    [
        {"query": ""},
        {"query": " "},
        {"query": "a" * 1001},
        {"query": "hi", "thread_id": "external"},
        {"query": "hi", "intent": "APPROVE"},
        {"query": "hi", "intent": "exec_shell"},
    ],
)
def test_run_schema_rejects_invalid_or_privileged_input(body):
    with pytest.raises(ValidationError):
        RunRequest.model_validate(body)


@pytest.mark.parametrize("intent", ["APPROVE", "persist_approved_tasks", "shell", "email"])
def test_model_cannot_route_to_privileged_actions(intent):
    with pytest.raises(ValidationError):
        IntentOutput(intent=intent)


@pytest.mark.parametrize(
    "tasks",
    [
        [],
        [{}] * 11,
        [
            {
                "title": "x",
                "description": "at 20 C",
                "source_quote": "at 80 C",
                "evidence_id": "E1",
                "basis": "documented",
            }
        ],
    ],
)
def test_draft_rejects_empty_oversized_or_misquoted_content(tasks):
    with pytest.raises(ValidationError):
        DraftContent(tasks=tasks)


def test_edit_and_approval_payloads_are_unambiguous():
    with pytest.raises(ValidationError):
        DecisionRequest(
            proposal_id=uuid4(),
            proposal_version=1,
            content_digest="a" * 64,
            decision="EDIT_AND_REVIEW",
        )


@pytest.mark.anyio
async def test_forged_tool_is_rejected_before_any_effect():
    tools = AgentTools(None, None, None, None)
    with pytest.raises(AgentError, match="tool_not_allowed"):
        await tools.invoke("exec_shell", {"command": "echo secret"})


def test_serializer_is_strict_without_pickle_fallback():
    serializer = strict_serializer()
    assert serializer.pickle_fallback is False
    assert serializer._allowed_msgpack_modules is None
    assert serializer._allowed_json_modules is None
    state = {"run_id": str(uuid4()), "tool_calls": 2, "intent": "tasks"}
    assert serializer.loads_typed(serializer.dumps_typed(state)) == state
    assert json.loads(json.dumps(state)) == state


@pytest.mark.anyio
async def test_real_langgraph_interrupt_restarts_node_prefix():
    from typing import TypedDict

    class State(TypedDict):
        approved: bool

    observations = []

    def review(state):
        observations.append("prefix")
        value = interrupt({"proposal": "test-only"})
        return {"approved": value}

    graph = (
        StateGraph(State)
        .add_node("review", review)
        .add_edge(START, "review")
        .add_edge("review", END)
        .compile(checkpointer=InMemorySaver(serde=strict_serializer()))
    )
    config = {"configurable": {"thread_id": "memory-only-test"}}
    paused = await graph.ainvoke({"approved": False}, config, durability="sync")
    assert paused["__interrupt__"]
    resumed = await graph.ainvoke(Command(resume=True), config, durability="sync")
    assert resumed["approved"] is True
    assert observations == ["prefix", "prefix"]
