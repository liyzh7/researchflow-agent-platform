"""Explicit deterministic provider for workflow infrastructure tests, never real-model metrics."""

import json

from researchflow.providers.llm import LLMRequest, LLMResponse
from researchflow.providers.mock import MockLLMProvider
from researchflow.reranking.types import RerankResponse, RerankScore
from tests.database_support import database_settings
from tests.support import DeterministicReranker

SOURCE_TEXT = (
    "Invented card-sort exercise: put one blue card in tray B, then count three wooden tokens. "
    "Record the tray label before counting."
)


class WorkflowTestProvider(MockLLMProvider):
    async def generate(self, request: LLMRequest, *, timeout_seconds: float):
        if request.response_schema_name == "researchflow_intent":
            query = request.prompt.casefold()
            intent = "tasks" if "task" in query or "任务" in query else "question"
            if "list" in query:
                intent = "list_tasks"
            content = json.dumps({"intent": intent})
        elif request.response_schema_name == "researchflow_task_draft":
            data = json.loads(request.prompt)
            evidence = data["untrusted_evidence"][0]
            quote = evidence["excerpt"]
            content = json.dumps(
                {
                    "tasks": [
                        {
                            "title": "[MOCK] Check tray label before token count",
                            "description": quote,
                            "evidence_id": evidence["evidence_id"],
                            "source_quote": quote,
                            "basis": "documented",
                        }
                    ]
                }
            )
        else:
            return await super().generate(request, timeout_seconds=timeout_seconds)
        return LLMResponse(
            content=content, provider="mock", model="workflow-test-only", is_mock=True
        )


class WorkflowTestReranker(DeterministicReranker):
    async def score(self, request, *, timeout_seconds):
        return RerankResponse(
            scores=tuple(
                RerankScore(segment_id=item.segment_id, score=8.0) for item in request.candidates
            ),
            model=self.info,
        )


def workflow_settings():
    return database_settings(
        "researchflow_agent_test",
        auth_login_per_minute=120,
        agent_enabled=True,
        rag_enabled=True,
        reranker_enabled=True,
        evidence_min_rerank_score=0.0,
        evidence_min_query_coverage=0.0,
        agent_access_secret="candidate-card-sort-capability-at-least-32-chars",
    )


def workflow_app(settings=None):
    from researchflow.main import create_app
    from tests.support import DeterministicEmbeddingProvider

    return create_app(
        settings=settings or workflow_settings(),
        llm_provider=WorkflowTestProvider(),
        embedding_provider=DeterministicEmbeddingProvider(),
        reranker_provider=WorkflowTestReranker(),
    )
