"""Application logging with request metadata but no request payloads."""

import json
import logging
import logging.config
import re
import time
from uuid import uuid4

from fastapi import FastAPI, Request
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from researchflow.auth.audit import emit_audit
from researchflow.auth.context import actor_scope
from researchflow.auth.context import request_id as request_id_context
from researchflow.auth.context import trace_id as trace_id_context
from researchflow.observability.tracing import active_telemetry

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _safe_route(request):
    path = getattr(request.scope.get("route"), "path", "unmatched")
    groups = {
        "auth",
        "chat",
        "documents",
        "retrieval",
        "rag",
        "agent",
        "tasks",
        "audit",
        "observability",
        "info",
    }
    segments = request.url.path.split("/")
    if (
        len(segments) > 3
        and segments[1:3] == ["api", "v1"]
        and segments[3] in groups
        and path != "unmatched"
    ):
        group = "/" + segments[3]
        return "/api/v1" + (path if path.startswith(group) else group + path)
    return path


class JsonFormatter(logging.Formatter):
    def format(self, record):
        # Third-party messages and traceback text are not controlled privacy surfaces.
        event = (
            record.getMessage() if record.name.startswith("researchflow.") else "third_party_log"
        )
        return json.dumps(
            {
                "timestamp": self.formatTime(record),
                "level": record.levelname,
                "logger": record.name,
                "event": event,
                "request_id": request_id_context.get(),
                "trace_id": trace_id_context.get(),
            },
            ensure_ascii=False,
        )


def configure_logging(level: str) -> None:
    """Configure concise structured-enough console logs for local services."""

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "()": JsonFormatter,
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                    "level": level,
                }
            },
            # HTTP clients log complete URLs at INFO, which can expose query
            # parameters. Provider integrations must opt in to safer metadata.
            "loggers": {
                "httpx2": {
                    "handlers": ["console"],
                    "level": "WARNING",
                    "propagate": False,
                },
                "httpcore2": {
                    "handlers": ["console"],
                    "level": "WARNING",
                    "propagate": False,
                },
            },
            "root": {"handlers": ["console"], "level": level},
        }
    )


def add_request_logging_middleware(app: FastAPI) -> None:
    """Log request metadata without headers, query strings, or bodies."""

    logger = logging.getLogger("researchflow.request")

    @app.middleware("http")
    async def log_request(request: Request, call_next):  # type: ignore[no-untyped-def]
        supplied_request_id = request.headers.get("x-request-id", "")
        request_id = (
            supplied_request_id if _SAFE_REQUEST_ID.fullmatch(supplied_request_id) else uuid4().hex
        )
        started_at = time.perf_counter()
        status_code = 500
        request_token = request_id_context.set(request_id)
        trace_token = trace_id_context.set(None)
        telemetry_token = active_telemetry.set(app.state.telemetry)
        # Propagate only traceparent; no baggage or arbitrary incoming attributes.
        incoming = TraceContextTextMapPropagator().extract(
            {"traceparent": request.headers.get("traceparent", "")}
        )
        try:
            with (
                actor_scope(None),
                app.state.telemetry.tracer.start_as_current_span(
                    "http.request",
                    context=incoming,
                    attributes={"http.request.method": request.method, "request.id": request_id},
                    record_exception=False,
                    set_status_on_exception=False,
                ) as span,
            ):
                trace_id_context.set(f"{span.get_span_context().trace_id:032x}")
                response = await call_next(request)
                status_code = response.status_code
                span.set_attribute("http.route", _safe_route(request))
                span.set_attribute("http.response.status_code", status_code)
                actor = getattr(request.state, "actor_id", None)
                if actor is not None:
                    span.set_attribute("actor.id", str(actor))
                if actor is not None and status_code in {403, 404, 409}:
                    event = "permission.denied"
                    if status_code == 409:
                        event = (
                            "approval.conflict"
                            if _safe_route(request).endswith("/decisions")
                            else "request.conflict"
                        )
                    await emit_audit(
                        app.state.database,
                        event,
                        "api",
                        outcome="denied",
                        actor_id=actor,
                        reason=f"http_{status_code}",
                    )
                response.headers["X-Request-ID"] = request_id
                response.headers["X-Trace-ID"] = f"{span.get_span_context().trace_id:032x}"
                return response
        finally:
            duration_ms = (time.perf_counter() - started_at) * 1000
            logger.info(
                "request.complete method=%s path=%s status=%d duration_ms=%.2f request_id=%s",
                request.method,
                _safe_route(request),
                status_code,
                duration_ms,
                request_id,
            )
            active_telemetry.reset(telemetry_token)
            request_id_context.reset(request_token)
            trace_id_context.reset(trace_token)
