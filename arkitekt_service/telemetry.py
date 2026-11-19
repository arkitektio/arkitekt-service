"""Traces and metrics of a service, to a collector the hub's operator names. Off until they do.

A service's ``asgi.py`` says, before anything else of Django's is imported::

    from arkitekt_service import telemetry

    telemetry.setup()
    ...
    application = telemetry.wrap(router(schema=schema, django_asgi_app=django_asgi_app))

and its schema carries :class:`arkitekt_service.graphql.TelemetryExtension`. The config decides
the rest (``telemetry`` of :class:`~arkitekt_service.server.settings.ServiceSettings`)::

    telemetry:
      endpoint: http://collector:4318

Without an endpoint nothing of OpenTelemetry's SDK is imported, no thread is started, and every
span and measurement in the code is the API's no-op. One thing still happens: a request that
arrives inside a trace (``traceparent``) keeps it, so what this service asks of others on its
behalf stays part of that trace where *they* record.

What is recorded when it is on: one span per HTTP request (not the health check, not a
websocket), one per GraphQL operation (the extension), one per database statement and per
outgoing httpx request. Over OTLP/HTTP only. A collector that wants credentials gets them from
OpenTelemetry's own ``OTEL_EXPORTER_OTLP_HEADERS``, so that no secret is part of the config.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterator, MutableMapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import context, propagate

from arkitekt_service.server.settings import ServiceSettings, TelemetrySettings

logger = logging.getLogger(__name__)

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
Application = Callable[[Scope, Receive, Send], Awaitable[None]]

#: The instrumentation scope a service's own spans and measurements are recorded under.
SCOPE = "arkitekt_service"
#: The request headers a trace travels in (W3C Trace Context).
TRACE_HEADERS = (b"traceparent", b"tracestate")
#: Requests nobody wants a span of: the health check, asked every few seconds.
EXCLUDED_URLS = r"/ht/?$"

_recording = False


def recording() -> bool:
    """Whether this process records and sends (:func:`setup` found an endpoint)."""
    return _recording


def _service_name() -> str:
    """What the hub calls this service: its contract's name."""
    from arkitekt_service.contract.contract import load

    return load().description.name


def setup(settings: TelemetrySettings | None = None, *, service_name: str | None = None) -> bool:
    """Start recording, when the config names a collector; whether it did.

    Called once, by the process that serves, before Django's application is built: a job
    (``migrate``) never imports ``asgi.py`` and so records nothing. A second call changes nothing.
    """
    global _recording
    if _recording:
        return True
    conf = settings if settings is not None else ServiceSettings().telemetry
    if not conf.endpoint:
        return False
    name = service_name or _service_name()
    base = conf.endpoint.rstrip("/")

    from opentelemetry import metrics, trace
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

    resource = Resource.create({"service.name": name})
    tracers = TracerProvider(sampler=ParentBased(TraceIdRatioBased(conf.sample_ratio)), resource=resource)
    tracers.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{base}/v1/traces")))
    trace.set_tracer_provider(tracers)
    if conf.metrics:
        reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=f"{base}/v1/metrics"))
        metrics.set_meter_provider(MeterProvider(metric_readers=[reader], resource=resource))
    PsycopgInstrumentor().instrument()
    HTTPXClientInstrumentor().instrument()
    _recording = True
    logger.info("Telemetry of %s goes to %s (%d%% of traces%s)", name, base, round(conf.sample_ratio * 100), ", with metrics" if conf.metrics else "")
    return True


def flush(timeout_millis: int = 5000) -> None:
    """Send what is buffered now (a test reading the collector; a process about to end)."""
    if not _recording:
        return
    from opentelemetry import metrics, trace
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.trace import TracerProvider

    tracers = trace.get_tracer_provider()
    if isinstance(tracers, TracerProvider):
        tracers.force_flush(timeout_millis)
    meters = metrics.get_meter_provider()
    if isinstance(meters, MeterProvider):
        meters.force_flush(timeout_millis)


def wrap(application: Application) -> Application:
    """The service's ASGI application, with its HTTP requests traced.

    Around the whole router: a GraphQL request is served by a consumer, and never passes
    Django's middleware. A websocket is left alone (a span as long as a connection says nothing).
    """
    traced = _traced(application) if _recording else _following(application)

    async def served(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            await traced(scope, receive, send)
        else:
            await application(scope, receive, send)

    return served


def _traced(application: Application) -> Application:
    from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware

    return OpenTelemetryMiddleware(application, excluded_urls=EXCLUDED_URLS, exclude_spans=["receive", "send"])


def _following(application: Application) -> Application:
    """Not recording: a request that came inside a trace still runs inside it, for what it asks of others."""

    async def followed(scope: Scope, receive: Receive, send: Send) -> None:
        carrier = {name.decode("latin-1"): value.decode("latin-1") for name, value in scope.get("headers", ()) if name in TRACE_HEADERS}
        if not carrier:
            await application(scope, receive, send)
            return
        token = context.attach(propagate.extract(carrier))
        try:
            await application(scope, receive, send)
        finally:
            context.detach(token)

    return followed


@contextmanager
def continuing(traceparent: str | None, name: str) -> Iterator[None]:
    """Run the block where ``traceparent`` says in a trace, as a span of this service called ``name``.

    For work that arrives with its place in a trace written in it (an ``ASSIGN``'s
    ``traceparent``), not in a request's headers. ``None`` continues nothing: where the service
    records, the block starts a trace of its own.
    """
    from opentelemetry import trace

    entered = context.attach(propagate.extract({"traceparent": traceparent})) if traceparent else None
    try:
        with trace.get_tracer(SCOPE).start_as_current_span(name):
            yield
    finally:
        if entered is not None:
            context.detach(entered)


def headers() -> dict[str, str]:
    """The headers that carry the running trace to whoever is asked next (none outside of one)."""
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier
