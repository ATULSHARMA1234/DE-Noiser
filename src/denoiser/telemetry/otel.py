"""SemanticOS tracing itself.

The platform ingests other people's OTLP spans and emits none of its own. So
when a query is slow, the only evidence is a latency histogram that says the
request took four seconds and nothing about where they went — API, Postgres or
ClickHouse. For a product whose pitch is that you should be able to see inside
your systems, that is a conspicuous gap, and it is the first thing an SRE
evaluating the platform notices.

This adds real spans across the tiers the platform owns:

* every HTTP request, via FastAPI instrumentation
* the SQLAlchemy queries underneath it
* outbound HTTP (the IdP, GitHub, webhooks, alert destinations)
* the ClickHouse reads and writes, which is where the time usually is

**Off unless configured.** No exporter endpoint means no tracing, and
`opentelemetry` is an optional dependency — the platform must not fail to start
because a telemetry package is missing. Both are checked once at startup and
reported, rather than at every request.

**Where the spans go.** To whatever OTLP endpoint is configured, which may
legitimately be this deployment's own ingest endpoint. That is supported but
worth thinking about before enabling: a platform tracing itself into itself
loses exactly the traces that explain its own outage, because the write path is
what is broken. Point it at a separate collector for anything you intend to
debug an incident with.
"""

from __future__ import annotations

import os
from typing import Any

from denoiser.logging import get_logger

logger = get_logger(__name__)

SERVICE_NAME = "semanticos"

#: Set to enable. Standard OTel variable, so an existing collector's
#: configuration works unchanged.
ENDPOINT_ENV = "OTEL_EXPORTER_OTLP_ENDPOINT"

_state: dict[str, Any] = {"configured": False, "enabled": False, "reason": None}


def tracing_endpoint() -> str:
    return os.getenv(ENDPOINT_ENV, "").strip()


def status() -> dict[str, Any]:
    """What self-tracing is doing, for the readiness and diagnostics surface."""
    return dict(_state)


def configure(app: Any = None, engine: Any = None) -> bool:
    """Set up tracing. Returns whether it was enabled.

    Never raises. A telemetry misconfiguration must not stop the API from
    serving — it is the layer that watches the work, not the work.
    """
    if _state["configured"]:
        return bool(_state["enabled"])
    _state["configured"] = True

    endpoint = tracing_endpoint()
    if not endpoint:
        _state["reason"] = f"{ENDPOINT_ENV} is not set"
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:
        # An optional dependency. Logged at warning rather than swallowed: an
        # operator who set the endpoint expects tracing, and silence would look
        # like a working exporter with no data.
        _state["reason"] = f"opentelemetry is not installed ({exc})"
        logger.warning(
            "%s is set but the OpenTelemetry SDK is not installed; self-tracing is off. "
            "Install the 'otel' extra to enable it.",
            ENDPOINT_ENV,
        )
        return False

    try:
        resource = Resource.create(
            {
                "service.name": os.getenv("OTEL_SERVICE_NAME", SERVICE_NAME),
                "service.version": os.getenv("SEMANTICOS_VERSION", "unknown"),
                "deployment.environment": os.getenv("SEMANTICOS_ENV", "development"),
            }
        )
        provider = TracerProvider(resource=resource)
        # Batched, not simple: a span export per request would put a network
        # round-trip on the request path, so the instrumentation would become
        # the latency it is meant to measure.
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(provider)

        _instrument(app, engine)
    except Exception as exc:
        _state["reason"] = str(exc)
        logger.warning("Self-tracing could not be configured: %s", exc)
        return False

    _state["enabled"] = True
    _state["reason"] = None
    logger.info("Self-tracing enabled, exporting to %s", endpoint)
    return True


def _instrument(app: Any, engine: Any) -> None:
    """Attach the per-library instrumentors that are installed.

    Each is attempted independently: a missing httpx instrumentor should not
    cost the FastAPI spans, which are the ones that make a trace navigable.
    """
    if app is not None:
        _try("fastapi", lambda: _instrument_fastapi(app))
    if engine is not None:
        _try("sqlalchemy", lambda: _instrument_sqlalchemy(engine))
    _try("httpx", _instrument_httpx)


def _try(name: str, action: Any) -> None:
    try:
        action()
        logger.debug("Instrumented %s", name)
    except ImportError:
        logger.debug("No %s instrumentation installed; skipping", name)
    except Exception as exc:
        logger.warning("Could not instrument %s: %s", name, exc)


def _instrument_fastapi(app: Any) -> None:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    # Health and metrics generate a span per scrape interval forever and
    # explain nothing. Excluding them keeps the trace store readable and its
    # bill finite.
    FastAPIInstrumentor.instrument_app(
        app, excluded_urls="health,health/live,health/ready,internal/metrics"
    )


def _instrument_sqlalchemy(engine: Any) -> None:
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    SQLAlchemyInstrumentor().instrument(engine=engine)


def _instrument_httpx() -> None:
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    HTTPXClientInstrumentor().instrument()


def tracer() -> Any:
    """A tracer for hand-written spans, or a no-op when tracing is off."""
    try:
        from opentelemetry import trace

        return trace.get_tracer(SERVICE_NAME)
    except ImportError:
        return _NoopTracer()


class _NoopSpan:
    def set_attribute(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def record_exception(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def __enter__(self) -> _NoopSpan:
        return self

    def __exit__(self, *_args: Any) -> bool:
        return False


class _NoopTracer:
    """Stands in when the SDK is absent, so callers need no import guard.

    `with tracer().start_as_current_span(...)` has to work whether or not
    OpenTelemetry is installed; the alternative is a conditional at every call
    site, which is how instrumentation ends up applied inconsistently.
    """

    def start_as_current_span(self, *_args: Any, **_kwargs: Any) -> _NoopSpan:
        return _NoopSpan()
