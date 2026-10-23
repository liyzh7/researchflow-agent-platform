"""Bounded provider execution with durable STARTED/finished receipts before validation."""

import asyncio
import time
from datetime import UTC, datetime

from opentelemetry import trace

from researchflow.auth.context import optional_actor, request_id, workflow_id
from researchflow.observability.models import ModelCall
from researchflow.observability.tracing import active_telemetry, operation
from researchflow.providers.errors import LLMProviderBusyError, LLMProviderTimeoutError


class ObservedLLMProvider:
    def __init__(self, provider, database, settings):
        self.inner = provider
        self.name = provider.name
        self.database = database
        self.settings = settings
        self._slots = asyncio.Semaphore(settings.llm_max_concurrency)
        self._in_system = 0
        self.peak_active = 0
        self.active = 0
        self.call_store = None  # Explicit Python injection seam for isolated storage tests.

    async def generate(self, request, *, timeout_seconds):
        if self._in_system >= self.settings.llm_max_concurrency + self.settings.llm_max_queue:
            raise LLMProviderBusyError("local model queue is full")
        self._in_system += 1
        acquired = False
        queued_at = time.perf_counter()
        try:
            # Waiting for a slot counts against the same end-to-end deadline.
            await asyncio.wait_for(self._slots.acquire(), timeout_seconds)
            acquired = True
            remaining = max(0.001, timeout_seconds - (time.perf_counter() - queued_at))
            return await self._invoke(request, remaining)
        except TimeoutError:
            raise LLMProviderTimeoutError from None
        finally:
            if acquired:
                self._slots.release()
            self._in_system -= 1

    async def _invoke(self, request, timeout_seconds):
        with operation("llm.generation", call_kind=request.call_kind):
            context = trace.get_current_span().get_span_context()
            row = ModelCall(
                actor_id=optional_actor(),
                workflow_id=workflow_id.get(),
                request_id=request_id.get(),
                trace_id=f"{context.trace_id:032x}" if context.is_valid else None,
                provider=self.inner.name,
                model=self.settings.llm_model,
                call_kind=request.call_kind,
                started_at=datetime.now(UTC),
                status="STARTED",
                token_usage_source="NOT_REPORTED",
                timing_source="NOT_REPORTED",
                warm_state="UNKNOWN",
            )
            if self.call_store is None:
                async with self.database.session() as session, session.begin():
                    session.add(row)
                    await session.flush()
            else:
                await self.call_store.start(row)
            started = time.perf_counter()
            output = None
            status = "FAILED"
            failure = None
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
            try:
                async with asyncio.timeout(timeout_seconds):
                    output = await self.inner.generate(request, timeout_seconds=timeout_seconds)
                status = "SUCCEEDED"
                return output
            except TimeoutError:
                status, failure = "TIMEOUT", "timeout"
                raise LLMProviderTimeoutError from None
            except asyncio.CancelledError:
                # Caller timeout/cancel is known locally. Hard process exit leaves STARTED instead.
                status, failure = "CANCELLED", "cancelled_or_deadline"
                raise
            except Exception as exc:
                category = getattr(exc, "category", None)
                failure = getattr(category, "value", "provider_error")
                status = "TIMEOUT" if failure == "timeout" else "FAILED"
                raise
            finally:
                self.active -= 1
                duration = (time.perf_counter() - started) * 1000

                async def finish():
                    if self.call_store is not None:
                        saved = row
                        saved.ended_at = datetime.now(UTC)
                        saved.duration_ms = duration
                        saved.timing_source = "LOCALLY_MEASURED"
                        saved.status, saved.failure_category = status, failure
                        saved.timed_out = status == "TIMEOUT"
                        if output is not None:
                            saved.model, saved.is_mock = output.model, output.is_mock
                            saved.input_tokens, saved.output_tokens = (
                                output.input_tokens,
                                output.output_tokens,
                            )
                            saved.token_usage_source = (
                                "PROVIDER_REPORTED"
                                if output.input_tokens is not None
                                and output.output_tokens is not None
                                else "NOT_REPORTED"
                            )
                        await self.call_store.finish(saved)
                        return
                    async with self.database.session() as session, session.begin():
                        saved = await session.get(ModelCall, row.id, with_for_update=True)
                        saved.ended_at = datetime.now(UTC)
                        saved.duration_ms = duration
                        saved.timing_source = "LOCALLY_MEASURED"
                        saved.status, saved.failure_category = status, failure
                        saved.timed_out = status == "TIMEOUT"
                        if output is not None:
                            saved.model, saved.is_mock = output.model, output.is_mock
                            saved.input_tokens, saved.output_tokens = (
                                output.input_tokens,
                                output.output_tokens,
                            )
                            saved.token_usage_source = (
                                "PROVIDER_REPORTED"
                                if output.input_tokens is not None
                                and output.output_tokens is not None
                                else "NOT_REPORTED"
                            )

                # Cancellation must not erase the attempt's finish record during ordinary timeout.
                await asyncio.shield(finish())
                telemetry = active_telemetry.get()
                if telemetry is not None:
                    labels = {"call_kind": request.call_kind, "status": status}
                    telemetry.calls.add(1, labels)
                    telemetry.latency.record(duration, labels)
