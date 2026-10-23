"""Small provider-neutral contract for text generation."""

from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class LLMRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    prompt: str = Field(min_length=1)
    system_prompt: str | None = Field(default=None, min_length=1)
    response_schema_name: str | None = Field(default=None, min_length=1, max_length=64)
    response_schema: dict[str, Any] | None = None
    call_kind: Literal["chat", "rag_answer", "agent_intent", "task_draft"] = "chat"


class LLMResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    content: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    is_mock: bool = False
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


@runtime_checkable
class LLMProvider(Protocol):
    """Contract implemented by model providers.

    Implementations must enforce ``timeout_seconds`` over the complete remote
    operation and translate SDK-specific failures into ``LLMProviderError``.
    """

    name: str

    async def generate(self, request: LLMRequest, *, timeout_seconds: float) -> LLMResponse: ...
