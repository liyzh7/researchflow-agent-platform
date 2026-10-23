"""Chat use case orchestration."""

from researchflow.providers.llm import LLMProvider, LLMRequest, LLMResponse


class ChatService:
    def __init__(self, provider: LLMProvider, *, timeout_seconds: float) -> None:
        self._provider = provider
        self._timeout_seconds = timeout_seconds

    async def answer(self, question: str) -> LLMResponse:
        request = LLMRequest(prompt=question)
        return await self._provider.generate(
            request,
            timeout_seconds=self._timeout_seconds,
        )
