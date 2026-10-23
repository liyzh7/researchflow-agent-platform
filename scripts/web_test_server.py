"""Isolated browser integration server; never imported by the product/Compose runtime."""

import os

from researchflow.core.config import Settings
from researchflow.main import create_app
from researchflow.providers.errors import LLMProviderTimeoutError
from tests.agent_support import WorkflowTestProvider, WorkflowTestReranker, workflow_app
from tests.support import DeterministicEmbeddingProvider


class RecoverableBrowserProvider(WorkflowTestProvider):
    """One controlled failure per synthetic request, only inside this test harness."""

    def __init__(self):
        self.failed_prompts = set()

    async def generate(self, request, *, timeout_seconds):
        if (
            request.response_schema_name == "researchflow_task_draft"
            and "P4_RECOVERY_ONCE" in request.prompt
            and request.prompt not in self.failed_prompts
        ):
            self.failed_prompts.add(request.prompt)
            raise LLMProviderTimeoutError
        return await super().generate(request, timeout_seconds=timeout_seconds)


def build():
    settings = Settings(_env_file=None)
    if settings.environment != "test" or settings.database_name not in {
        "researchflow_web_e2e_test",
        "researchflow_web_real_test",
    }:
        raise RuntimeError("browser tests require their dedicated database")
    if os.environ.get("RESEARCHFLOW_WEB_INFERENCE") == "real":
        return create_app(settings)
    if os.environ.get("RESEARCHFLOW_WEB_INJECT_FAILURES") == "1":
        return create_app(
            settings=settings,
            llm_provider=RecoverableBrowserProvider(),
            embedding_provider=DeterministicEmbeddingProvider(),
            reranker_provider=WorkflowTestReranker(),
        )
    return workflow_app(settings)
