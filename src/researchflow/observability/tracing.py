"""Manual OTel spans without prompt/SQL/header capture or cloud exporters."""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

from langgraph.errors import GraphBubbleUp
from opentelemetry import trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Status, StatusCode

from researchflow.auth.context import optional_actor, request_id

active_telemetry: ContextVar["Telemetry | None"] = ContextVar(
    "researchflow_telemetry", default=None
)


class Telemetry:
    def __init__(self, max_spans=512):
        self.exporter = InMemorySpanExporter(max_spans=max_spans)
        resource = Resource({"service.name": "researchflow"})
        self.provider = TracerProvider(resource=resource)
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.tracer = self.provider.get_tracer("researchflow.manual", "0.7.0")
        self.metric_reader = InMemoryMetricReader()
        self.meter_provider = MeterProvider(resource=resource, metric_readers=[self.metric_reader])
        meter = self.meter_provider.get_meter("researchflow")
        self.calls = meter.create_counter("researchflow.model.calls")
        self.latency = meter.create_histogram("researchflow.model.duration", unit="ms")

    def close(self):
        self.provider.shutdown()
        self.meter_provider.shutdown()


@contextmanager
def operation(name, **attributes):
    telemetry = active_telemetry.get()
    if telemetry is None:
        yield trace.get_current_span()
        return
    safe = dict(attributes)
    actor = optional_actor()
    if actor is not None:
        safe["actor.id"] = str(actor)
    if request_id.get() is not None:
        safe["request.id"] = request_id.get()
    with telemetry.tracer.start_as_current_span(
        name, attributes=safe, record_exception=False, set_status_on_exception=False
    ) as span:
        try:
            yield span
        except GraphBubbleUp:
            span.add_event("workflow.paused")
            raise
        except BaseException as exc:
            # Exception text/traceback can contain prompts, SQL parameters or credentials.
            category = type(exc).__name__[:64]
            span.set_attribute("error.type", category)
            span.set_status(Status(StatusCode.ERROR))
            span.add_event("operation.failed", {"error.type": category})
            raise


def traced(name):
    def decorate(function):
        @wraps(function)
        async def invoke(*args, **kwargs):
            with operation(name):
                return await function(*args, **kwargs)

        return invoke

    return decorate
