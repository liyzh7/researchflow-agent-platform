"""Opt-in structured generation against an approved loopback-only LLM."""

import json
import os

import pytest

from researchflow.core.config import Settings
from researchflow.providers.llm import LLMRequest
from researchflow.providers.openai_compatible import OpenAICompatibleLLMProvider

pytestmark = [
    pytest.mark.real_llm,
    pytest.mark.skipif(
        os.getenv("RESEARCHFLOW_RUN_REAL_LLM") != "1",
        reason="set RESEARCHFLOW_RUN_REAL_LLM=1 after local model approval",
    ),
]


@pytest.mark.anyio
async def test_approved_local_llm_enforces_structured_output() -> None:
    base_url = os.environ["RESEARCHFLOW_REAL_LLM_BASE_URL"]
    model = os.environ["RESEARCHFLOW_REAL_LLM_MODEL"]
    settings = Settings(
        environment="test",
        database_password="real-llm-test-only",
        llm_provider_mode="openai_compatible",
        llm_base_url=base_url,
        llm_model=model,
        llm_timeout_seconds=120,
        llm_allow_remote=False,
        _env_file=None,
    )

    response = await OpenAICompatibleLLMProvider(settings).generate(
        LLMRequest(
            prompt=(
                "Use the response schema to encode the success status "
                "for an invented card-sort check."
            ),
            system_prompt="Emit a single JSON object with precisely the schema fields.",
            response_schema_name="local_llm_smoke",
            response_schema={
                "type": "object",
                "properties": {"status": {"type": "string", "enum": ["ok"]}},
                "required": ["status"],
                "additionalProperties": False,
            },
        ),
        timeout_seconds=settings.llm_timeout_seconds,
    )

    assert json.loads(response.content) == {"status": "ok"}
    assert response.provider == "openai_compatible"
    assert response.model == settings.llm_model
    assert response.is_mock is False
    assert response.input_tokens is not None and response.input_tokens > 0
    assert response.output_tokens is not None and response.output_tokens > 0
