"""Deterministic local LLM implementation for tests and initial development."""

import json
from hashlib import sha256

from researchflow.providers.errors import LLMProviderInvalidRequestError
from researchflow.providers.llm import LLMRequest, LLMResponse


class MockLLMProvider:
    name = "mock"

    async def generate(self, request: LLMRequest, *, timeout_seconds: float) -> LLMResponse:
        if timeout_seconds <= 0:
            raise LLMProviderInvalidRequestError("timeout_seconds must be positive")

        digest = sha256(request.prompt.encode("utf-8")).hexdigest()[:12]
        if request.response_schema is not None:
            evidence_ids = (
                request.response_schema.get("properties", {})
                .get("citations", {})
                .get("items", {})
                .get("properties", {})
                .get("evidence_id", {})
                .get("enum", [])
            )
            payload = {
                "answer": f"[MOCK] Deterministic grounded test response ({digest}).",
                "citations": ([{"evidence_id": str(evidence_ids[0])}] if evidence_ids else []),
            }
            content = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        else:
            content = (
                f"[MOCK] Deterministic test response ({digest}). No model inference was performed."
            )
        return LLMResponse(
            content=content,
            provider=self.name,
            model="deterministic-mock-v1",
            is_mock=True,
        )
