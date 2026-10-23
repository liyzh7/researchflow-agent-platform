"""Network-free deterministic tests for limits, cancellation and privacy-safe spans."""

import asyncio
import threading

import pytest

from researchflow.core.resources import run_bounded_thread
from researchflow.observability.llm import ObservedLLMProvider
from researchflow.observability.tracing import Telemetry, active_telemetry, operation
from researchflow.providers.errors import LLMProviderBusyError
from researchflow.providers.llm import LLMRequest, LLMResponse
from tests.auth_support import MemoryCallStore


@pytest.mark.anyio
async def test_llm_admission_limit_is_bounded_and_recovers(settings):
    class SlowProvider:
        name = "mock-resource-test"

        async def generate(self, request, *, timeout_seconds):
            await asyncio.sleep(0.03)
            return LLMResponse(
                content="[MOCK] A generated test-only square",
                provider=self.name,
                model="mock",
                is_mock=True,
            )

    settings.llm_max_concurrency = 1
    settings.llm_max_queue = 2
    provider = ObservedLLMProvider(SlowProvider(), None, settings)
    provider.call_store = MemoryCallStore()
    results = await asyncio.gather(
        *[provider.generate(LLMRequest(prompt="test"), timeout_seconds=1) for _ in range(10)],
        return_exceptions=True,
    )
    assert sum(isinstance(result, LLMResponse) for result in results) == 3
    assert sum(isinstance(result, LLMProviderBusyError) for result in results) == 7
    assert provider.peak_active == 1 and provider._in_system == 0
    assert (
        len(provider.call_store.rows) == 3
    )  # Rejected admission is not a fabricated provider call.
    recovered = await provider.generate(LLMRequest(prompt="test"), timeout_seconds=1)
    assert recovered.is_mock


@pytest.mark.anyio
async def test_timeout_does_not_release_cpu_permit_while_native_thread_runs():
    slots = asyncio.Semaphore(1)
    completed = threading.Event()

    def operation():
        completed.wait(2)
        return "finished"

    try:
        with pytest.raises(TimeoutError):
            await run_bounded_thread(slots, operation, 0.03)
        assert slots.locked()
        with pytest.raises(TimeoutError):
            await run_bounded_thread(slots, lambda: "must not start", 0.03)
    finally:
        completed.set()
        for _ in range(100):
            if not slots.locked():
                break
            await asyncio.sleep(0.01)
    assert not slots.locked()


def test_span_error_records_type_not_exception_payload():
    telemetry = Telemetry(max_spans=32)
    token = active_telemetry.set(telemetry)
    try:
        with pytest.raises(ValueError), operation("synthetic.validation"):
            raise ValueError("private-token-prompt-document-payload")
        spans = telemetry.exporter.get_finished_spans()
        assert len(spans) == 1
        encoded = spans[0].to_json()
        assert "private-token-prompt-document-payload" not in encoded
        assert "ValueError" in encoded
    finally:
        active_telemetry.reset(token)
        telemetry.close()


def test_local_exporter_has_bounded_retention():
    telemetry = Telemetry(max_spans=32)
    for _ in range(100):
        with telemetry.tracer.start_as_current_span("synthetic"):
            pass
    assert len(telemetry.exporter.get_finished_spans()) == 32
    telemetry.close()
