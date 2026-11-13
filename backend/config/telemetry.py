"""Sentry (EU) and OpenTelemetry to Honeycomb (EU), set up once per process.

`setup("api")` runs in config/asgi.py and `setup("beat")` in Celery Beat. A Celery
worker starts Sentry in its main process and OpenTelemetry in each pool process
(config/celery.py). Each part is a no-op when its key is unset, as in local
development and CI:

- Sentry needs `SENTRY_DSN` on an EU host (`*.de.sentry.io`). Errors only: tracing is
  Honeycomb's job, and personal data is removed by config/scrubbing.py.
- OpenTelemetry needs `OTEL_EXPORTER_OTLP_HEADERS` (`x-honeycomb-team=<key>`). The
  exporter reads `OTEL_EXPORTER_OTLP_ENDPOINT` (https://api.eu1.honeycomb.io) and the
  resource reads `OTEL_SERVICE_NAME` (taskbeavo-api, -worker or -beat).

Every exported span passes `AllowListSpanProcessor`: attributes not listed in
`ALLOWED_SPAN_ATTRIBUTES` are dropped, so no instrumentation can send a query string,
a SQL statement, a GraphQL document or its variables, a client IP or a user agent.
Adding a key needs a review (observability skill), here and in
packages/graphql/src/telemetry.ts.
"""

import logging
import os
import re
from typing import Literal
from urllib.parse import urlsplit

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.sdk.trace import Event, ReadableSpan, Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.trace import Link, Status
from opentelemetry.util.types import Attributes, AttributeValue

Role = Literal["api", "worker", "beat"]

logger = logging.getLogger(__name__)

# Sentry's EU data region (CLAUDE.md): its DSNs point at *.ingest.de.sentry.io.
SENTRY_EU_HOST_SUFFIX = ".de.sentry.io"

# Keys our code sets (observability skill), then the instrumentations' technical keys
# that carry no personal data.
ALLOWED_SPAN_ATTRIBUTES = frozenset(
    {
        # TaskBeavo spans: internal IDs, enum keys and amounts.
        "user.id",
        "task.id",
        "offer.id",
        "booking.id",
        "outbox.id",
        "upload.id",
        "upload.purpose",
        "upload.status",
        "booking.status",
        "booking.transition",
        "stripe.event_type",
        "stripe.event_id",
        "stripe.object_id",
        "stripe.account_id",
        "helper_profile.id",
        "amount.cents",
        "currency",
        "graphql.operation.name",
        "graphql.operation.type",
        "notification.channel",
        "notification.topic",
        "notification.kind",
        "notification.status",
        "locale",
        "app.platform",
        "app.version",
        "fiscal.receipt_id",
        "fiscal.status",
        # Strawberry's resolver spans (never `query` or `graphql.param.*`).
        "component",
        "graphql.parentType",
        "graphql.path",
        # Django and ASGI (old and stable HTTP semantic conventions). No URL, target,
        # query string, client address or user agent.
        "http.method",
        "http.request.method",
        "http.route",
        "http.status_code",
        "http.response.status_code",
        "http.scheme",
        "url.scheme",
        "http.flavor",
        "network.protocol.version",
        "http.server_name",
        "server.address",
        "server.port",
        "net.host.name",
        "net.host.port",
        "asgi.event.type",
        "websocket.close.code",
        # Outgoing HTTP (Stripe, Resend, Expo, CIS): the host, never the URL.
        "net.peer.name",
        "net.peer.port",
        # psycopg and Redis: which database, never the statement or its arguments.
        "db.system",
        "db.system.name",
        "db.name",
        "db.namespace",
        "db.redis.database_index",
        "db.operation",
        "db.operation.name",
        "db.redis.args_length",
        "db.redis.pipeline_length",
        # Celery: task names, IDs and delivery facts; task arguments are never recorded.
        "celery.action",
        "celery.task_name",
        "celery.state",
        "celery.retries",
        "celery.hostname",
        "messaging.system",
        "messaging.destination",
        "messaging.destination_kind",
        "messaging.destination.name",
        "messaging.message_id",
        "messaging.message.id",
        "messaging.operation",
        "messaging.conversation_id",
        # Errors: the type only. Messages and stack traces can quote personal data.
        "error.type",
        "exception.type",
        "exception.escaped",
    }
)
# A span's status description keeps an exception class name, never its message.
_STATUS_DESCRIPTION = re.compile(r"[A-Za-z_][\w.]{0,199}")


def allowed_attributes(attributes: Attributes) -> dict[str, AttributeValue]:
    return {
        key: value for key, value in (attributes or {}).items() if key in ALLOWED_SPAN_ATTRIBUTES
    }


def allow_listed(span: ReadableSpan) -> ReadableSpan:
    """A copy of `span` with only allow-listed attributes on it, its events and links."""
    status = span.status
    description = status.description or ""
    if description and not _STATUS_DESCRIPTION.fullmatch(description):
        # `record_exception` and instrumentations write "Type: message".
        head = description.split(":", 1)[0].strip()
        description = head if _STATUS_DESCRIPTION.fullmatch(head) else ""
    return ReadableSpan(
        name=span.name,
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=allowed_attributes(span.attributes),
        events=[
            Event(event.name, allowed_attributes(event.attributes), event.timestamp)
            for event in span.events
        ],
        links=[Link(link.context, allowed_attributes(link.attributes)) for link in span.links],
        kind=span.kind,
        status=Status(status.status_code, description or None),
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


class AllowListSpanProcessor(SpanProcessor):
    """Hands an allow-listed copy of each finished span to the exporting processor."""

    def __init__(self, delegate: SpanProcessor) -> None:
        self._delegate = delegate

    def on_start(self, span: Span, parent_context: otel_context.Context | None = None) -> None:
        self._delegate.on_start(span, parent_context=parent_context)

    def on_end(self, span: ReadableSpan) -> None:
        self._delegate.on_end(allow_listed(span))

    def shutdown(self) -> None:
        self._delegate.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._delegate.force_flush(timeout_millis)


def build_tracer_provider(exporter: SpanExporter) -> TracerProvider:
    """A provider that exports allow-listed spans in batches. The resource reads
    `OTEL_SERVICE_NAME` and `OTEL_RESOURCE_ATTRIBUTES`."""
    provider = TracerProvider()
    provider.add_span_processor(AllowListSpanProcessor(BatchSpanProcessor(exporter)))
    return provider


_configured: set[str] = set()


def setup(role: Role, *, sentry: bool = True, tracing: bool = True) -> None:
    """Start Sentry and OpenTelemetry for this process, each once and only when its key
    is set. A Celery worker starts the two at different moments (config/celery.py)."""
    if sentry and "sentry" not in _configured:
        _configured.add("sentry")
        setup_sentry()
    if tracing and "tracing" not in _configured:
        _configured.add("tracing")
        setup_tracing(role)


def setup_sentry(**overrides: object) -> bool:
    """Start Sentry when `SENTRY_DSN` is set. `overrides` are for tests (a transport)."""
    from django.conf import settings

    dsn = overrides.pop("dsn", settings.SENTRY_DSN)
    if not dsn:
        return False
    dsn = str(dsn)
    host = urlsplit(dsn).hostname or ""
    if not host.endswith(SENTRY_EU_HOST_SUFFIX):
        # The DSN host decides the data region. The DSN itself is not logged.
        logger.error(
            "SENTRY_DSN is not in Sentry's EU region (*%s); Sentry stays off",
            SENTRY_EU_HOST_SUFFIX,
        )
        return False

    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.redis import RedisIntegration

    from config.scrubbing import before_breadcrumb, before_send

    sentry_sdk.init(
        dsn=dsn,
        environment=settings.SENTRY_ENVIRONMENT,
        release=settings.SENTRY_RELEASE or None,
        integrations=[
            DjangoIntegration(),
            CeleryIntegration(),
            RedisIntegration(),
        ],
        # Only the integrations above (plus the SDK defaults such as logging). Sentry's
        # Strawberry integration would patch every Schema to run async only and mute the
        # "strawberry.execution" log, through which resolver crashes reach Sentry here.
        auto_enabling_integrations=False,
        # Personal data stays out: the SDK collects no user details, cookies, headers,
        # bodies, query strings, GraphQL documents or variables, SQL parameters or local
        # variables (`data_collection` replaces `send_default_pii=False`), and
        # config/scrubbing.py checks every event before it leaves.
        data_collection={
            "user_info": False,
            "cookies": {"mode": "off"},
            "http_headers": {"request": {"mode": "off"}},
            "http_bodies": [],
            "url_query_params": {"mode": "off"},
            "graphql": {"document": False, "variables": False},
            "gen_ai": {"inputs": False, "outputs": False},
            "database_query_data": False,
            "queues": False,
            "stack_frame_variables": False,
        },
        max_request_body_size="never",
        include_local_variables=False,
        before_send=before_send,  # pyright: ignore[reportArgumentType]
        before_breadcrumb=before_breadcrumb,  # pyright: ignore[reportArgumentType]
        # Errors only. Traces go to Honeycomb; Sentry's own tracing and logs stay off.
        traces_sample_rate=0.0,
        enable_logs=False,
        **overrides,  # pyright: ignore[reportArgumentType]
    )
    return True


def setup_tracing(role: Role, exporter: SpanExporter | None = None) -> bool:
    """Export traces to Honeycomb when `OTEL_EXPORTER_OTLP_HEADERS` (the API key) is set."""
    if exporter is None:
        if not os.environ.get("OTEL_EXPORTER_OTLP_HEADERS"):
            return False
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        exporter = OTLPSpanExporter()
    trace.set_tracer_provider(build_tracer_provider(exporter))
    instrument(role)
    return True


def instrument(role: Role) -> None:
    """Turn on the library instrumentations. Celery publishes from every role."""
    from opentelemetry.instrumentation.celery import CeleryInstrumentor
    from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    from opentelemetry.instrumentation.requests import RequestsInstrumentor

    if role == "api":
        from opentelemetry.instrumentation.django import DjangoInstrumentor

        # Must run before Django loads its middleware (config/asgi.py).
        DjangoInstrumentor().instrument(is_sql_commentor_enabled=False)
    CeleryInstrumentor().instrument()
    PsycopgInstrumentor().instrument(enable_commenter=False)
    RedisInstrumentor().instrument()
    RequestsInstrumentor().instrument()
