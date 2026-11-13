"""Sentry and Honeycomb: one trace from request to Celery, and no personal data leaves.

Spans are read from the conftest exporter, which sits behind the production
allow-list. Sentry events are read from a transport that records what would be sent.
"""

import json
import logging
import os
import random
import re
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import sentry_sdk
from django.db import IntegrityError, transaction
from django.test import Client
from django.utils import timezone
from graphql import GraphQLError
from opentelemetry import trace
from opentelemetry.instrumentation.celery import CeleryInstrumentor
from opentelemetry.instrumentation.django import DjangoInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, Status, StatusCode
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport

from accounts.tests.factories import access_token
from config import telemetry
from config.scrubbing import (
    FILTERED,
    before_breadcrumb,
    before_send,
    scrub_id,
    scrub_text,
)
from config.tests.telemetry_urls import TRACE_TOPIC
from core import outbox
from core.errors import ErrorCode, SafeMessageError
from core.i18n import recipient_locale
from core.models import Outbox
from core.synthetic_identifiers import synthetic_iban, synthetic_oib

tracer = trace.get_tracer(__name__)

NAME = "Ana Horvat"
EMAIL = "ana.horvat@example.com"
OIB = synthetic_oib(69)
IBAN = synthetic_iban(160)
ADDRESS = "Testna ulica 12, Dubrovnik"
PHONE = "+385 91 234 5678"
CHAT = "Ključ je ispod otirača"
PII = {
    "name": NAME,
    "email": EMAIL,
    "oib": OIB,
    "iban": IBAN,
    "address": ADDRESS,
    "phone": PHONE,
    "chat text": CHAT,
}

INBOUND_TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
INBOUND_TRACEPARENT = f"00-{INBOUND_TRACE_ID}-00f067aa0ba902b7-01"


def user_fields(user_model: Any, email: str) -> dict[str, Any]:
    """Every field accounts.User requires, for a raw `objects.create`."""
    return {
        user_model.USERNAME_FIELD: email,
        user_model.get_email_field_name(): email,
        "first_name": "Ana",
        "last_name": "Test",
        "locale": "hr",
        "country": "HR",
        "time_zone": "Europe/Zagreb",
        "adult_confirmed_at": timezone.now(),
    }


def assert_no_pii(sent: Any) -> None:
    text = json.dumps(sent, default=str, ensure_ascii=False)
    leaked = [label for label, value in PII.items() if value in text]
    # The phone number also appears without spaces in some places.
    if PHONE.replace(" ", "") in text:
        leaked.append("phone")
    assert not leaked, f"{leaked} reached the telemetry vendor"


# ------------------------------------------------------------------ Honeycomb (spans)


def test_span_allow_list_drops_unknown_attributes_events_and_messages(spans) -> None:
    with tracer.start_as_current_span("booking.release") as span:
        span.set_attribute("booking.id", "42")
        span.set_attribute("booking.status", "FUNDS_HELD")
        span.set_attribute("http.route", "graphql")
        span.set_attribute("http.url", f"https://api.taskbeavo.hr/graphql?email={EMAIL}")
        span.set_attribute("db.statement", f"SELECT 1 WHERE oib = '{OIB}'")
        span.set_attribute("query", f'mutation {{ send(text: "{CHAT}") }}')
        span.set_attribute("graphql.param.iban", IBAN)
        span.set_attribute("client.address", "203.0.113.7")
        span.set_attribute("user_agent.original", "Mozilla/5.0")
        span.set_attribute("enduser.name", NAME)
        span.add_event("note", {"address": ADDRESS, "booking.id": "42"})
        span.add_link(span.get_span_context(), {"phone": PHONE, "outbox.id": "7"})
        span.record_exception(ValueError(f"{NAME} {PHONE}"))
        span.set_status(Status(StatusCode.ERROR, f"ValueError: {NAME} {PHONE}"))

    (exported,) = spans.get_finished_spans()
    assert dict(exported.attributes or {}) == {
        "booking.id": "42",
        "booking.status": "FUNDS_HELD",
        "http.route": "graphql",
    }
    note, exception = exported.events
    assert dict(note.attributes or {}) == {"booking.id": "42"}
    assert dict(exception.attributes or {}) == {
        "exception.type": "ValueError",
        "exception.escaped": "False",
    }
    assert dict(exported.links[0].attributes or {}) == {"outbox.id": "7"}
    assert exported.status.description == "ValueError"
    assert_no_pii(exported.to_json())


def test_status_description_without_a_type_is_dropped(spans) -> None:
    with tracer.start_as_current_span("work") as span:
        span.set_status(Status(StatusCode.ERROR, f"{CHAT}"))

    (exported,) = spans.get_finished_spans()
    assert exported.status.status_code == StatusCode.ERROR
    assert exported.status.description is None


def test_graphql_spans_carry_neither_the_document_nor_arguments(spans) -> None:
    from config.tests.telemetry_urls import schema

    schema.execute_sync(f'mutation Send {{ crash(chatText: "{CHAT}", oib: "{OIB}") }}')

    names = {span.name for span in spans.get_finished_spans()}
    assert "GraphQL Query: Send" in names
    for span in spans.get_finished_spans():
        assert "query" not in (span.attributes or {})
        assert not [key for key in span.attributes or {} if key.startswith("graphql.param.")]
        assert_no_pii(span.to_json())


@pytest.fixture
def instrumented() -> Iterator[None]:
    """The api's Django and Celery instrumentation, on the conftest provider."""
    DjangoInstrumentor().instrument(is_sql_commentor_enabled=False)
    CeleryInstrumentor().instrument()
    yield
    CeleryInstrumentor().uninstrument()
    DjangoInstrumentor().uninstrument()


@pytest.fixture
def published(monkeypatch) -> list[int]:
    rows: list[int] = []
    monkeypatch.setitem(outbox._handlers, TRACE_TOPIC, lambda row: rows.append(row.pk))
    return rows


@pytest.mark.urls("config.tests.telemetry_urls")
@pytest.mark.django_db(transaction=True)
def test_request_and_its_celery_task_share_one_trace(
    django_user_model, instrumented, published, spans
) -> None:
    """Client traceparent → Django → GraphQL → outbox row → Celery task → publish span."""
    user = django_user_model.objects.create(**user_fields(django_user_model, "poster@example.com"))
    client = Client()

    response = client.post(
        "/graphql",
        {"query": f'mutation Notify {{ notify(note: "{CHAT}") }}'},
        content_type="application/json",
        headers={
            "authorization": f"Bearer {access_token(user)}",
            "traceparent": INBOUND_TRACEPARENT,
        },
    )

    assert response.status_code == 200
    row_id = response.json()["data"]["notify"]
    assert published == [row_id]
    assert Outbox.objects.get(pk=row_id).traceparent.split("-")[1] == INBOUND_TRACE_ID

    finished = spans.get_finished_spans()
    by_name = {span.name: span for span in finished}
    request_span = next(s for s in finished if s.kind == SpanKind.SERVER and "graphql" in s.name)
    assert request_span.parent is not None
    assert request_span.parent.span_id == 0x00F067AA0BA902B7
    assert "GraphQL Query: Notify" in by_name
    assert "run/core.outbox.publish" in by_name, sorted(by_name)
    assert "outbox.publish" in by_name
    # One trace in Honeycomb: the request, the GraphQL operation, the Celery task and
    # the outbox publish all carry the client's trace ID.
    assert {f"{span.context.trace_id:032x}" for span in finished} == {INBOUND_TRACE_ID}
    for span in finished:
        assert_no_pii(span.to_json())


def test_tracing_is_off_without_the_honeycomb_key(monkeypatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_HEADERS", raising=False)
    provider = trace.get_tracer_provider()

    assert telemetry.setup_tracing("api") is False
    assert trace.get_tracer_provider() is provider


def test_provider_exports_allow_listed_spans() -> None:
    exporter = InMemorySpanExporter()
    provider = telemetry.build_tracer_provider(exporter)
    assert isinstance(provider, TracerProvider)

    with provider.get_tracer(__name__).start_as_current_span("x") as span:
        span.set_attribute("booking.id", "1")
        span.set_attribute("email", EMAIL)
    provider.force_flush()

    (exported,) = exporter.get_finished_spans()
    assert dict(exported.attributes or {}) == {"booking.id": "1"}
    provider.shutdown()


# ------------------------------------------------------------------------ Sentry


class RecordingTransport(Transport):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict[str, Any]] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        if (event := envelope.get_event()) is not None:
            self.events.append(dict(event))


@pytest.fixture
def sentry_events(settings) -> Iterator[list[dict[str, Any]]]:
    transport = RecordingTransport()
    assert telemetry.setup_sentry(
        dsn="https://public@o1.ingest.de.sentry.io/1", transport=transport
    )
    yield transport.events
    sentry_sdk.get_client().close()
    # Back to an inactive client, without auto-enabling integrations for later tests.
    sentry_sdk.init(auto_enabling_integrations=False, default_integrations=False)


def test_sentry_is_off_without_a_dsn(settings, monkeypatch) -> None:
    settings.SENTRY_DSN = ""
    inits: list[object] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda *args, **kwargs: inits.append(kwargs))

    assert telemetry.setup_sentry() is False
    assert inits == []


def test_sentry_refuses_a_dsn_outside_the_eu(monkeypatch, caplog) -> None:
    us_dsn = "https://public@o1.ingest.us.sentry.io/1"
    inits: list[object] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda *args, **kwargs: inits.append(kwargs))

    with caplog.at_level(logging.ERROR, logger="config.telemetry"):
        assert telemetry.setup_sentry(dsn=us_dsn) is False

    assert inits == []
    (record,) = caplog.records
    assert "EU region" in record.getMessage()
    assert us_dsn not in caplog.text


# A fresh worker process: the Celery integration patches each tracer only once per
# process, so earlier tests must not have done it already.
WORKER_SETUP = textwrap.dedent(
    """
    import functools

    import django
    django.setup()
    import sentry_sdk
    from celery.signals import celeryd_init, worker_process_init
    from sentry_sdk.transport import Transport

    from config import telemetry
    from config.celery import app

    class DropEvents(Transport):
        def capture_envelope(self, envelope):
            pass

    # Nothing is sent from this test.
    telemetry.setup_sentry = functools.partial(telemetry.setup_sentry, transport=DropEvents())
    app.loader.import_default_modules()
    app.finalize()
    task = app.tasks["core.outbox.publish"]

    def build_tracers():
        # What celery.concurrency.prefork.process_initializer does in each pool
        # process before it sends worker_process_init, importing at call time too.
        from celery.app.trace import build_tracer

        for name, each in app.tasks.items():
            each.__trace__ = build_tracer(name, each, app.loader, "test", app=app)

    build_tracers()
    assert not getattr(task, "_sentry_is_patched", False)
    # The worker's main process, before the pool forks.
    celeryd_init.send(sender="test", instance=None, conf=app.conf, options={})
    assert sentry_sdk.get_client().is_active()
    # Each pool process, after the fork.
    build_tracers()
    worker_process_init.send(sender=None)
    assert getattr(task, "_sentry_is_patched", False), "tasks are not wrapped by Sentry"
    print("patched")
    """
)


def test_worker_main_process_starts_sentry_before_tasks_get_their_tracers() -> None:
    env = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "config.settings.test",
        "SENTRY_DSN": "https://public@o1.ingest.de.sentry.io/1",
    }
    env.pop("OTEL_EXPORTER_OTLP_HEADERS", None)

    result = subprocess.run(  # noqa: S603 - our own interpreter and script.
        [sys.executable, "-c", WORKER_SETUP],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("patched")


def test_exception_message_and_local_variables_never_leave(sentry_events) -> None:
    oib = OIB  # noqa: F841 - a local variable Sentry must not see.
    try:
        raise ValueError(f"{NAME}, {EMAIL}, {IBAN}, {ADDRESS}, {PHONE}: {CHAT}")
    except ValueError:
        sentry_sdk.capture_exception()

    (event,) = sentry_events
    (exception,) = event["exception"]["values"]
    assert exception["type"] == "ValueError"
    assert exception["value"] == FILTERED
    assert all("vars" not in frame for frame in exception["stacktrace"]["frames"])
    assert_no_pii(event)


class PayoutFailedError(SafeMessageError):
    pass


def test_safe_message_error_keeps_its_message_and_tags(sentry_events) -> None:
    try:
        raise PayoutFailedError(
            "Booking 42 payout failed in FUNDS_HELD",
            code=ErrorCode.RATE_LIMITED,
            ids={"booking.id": 42, "stripe.event_id": "evt_1", "email": EMAIL},
        )
    except PayoutFailedError:
        sentry_sdk.capture_exception()

    (event,) = sentry_events
    (exception,) = event["exception"]["values"]
    assert exception["value"] == "Booking 42 payout failed in FUNDS_HELD"
    assert event["tags"]["error.code"] == "RATE_LIMITED"
    assert event["tags"]["booking.id"] == "42"
    assert event["tags"]["stripe.event_id"] == "evt_1"
    assert "email" not in event["tags"]
    assert_no_pii(event)


def test_safe_message_error_that_breaks_the_contract_is_still_scrubbed(sentry_events) -> None:
    try:
        raise SafeMessageError(f"Booking 42 for {EMAIL}")
    except SafeMessageError:
        sentry_sdk.capture_exception()

    (event,) = sentry_events
    assert event["exception"]["values"][0]["value"] == f"Booking 42 for {FILTERED}"


@pytest.mark.django_db
def test_integrity_error_is_filtered_but_tagged_with_its_constraint(
    django_user_model, sentry_events
) -> None:
    django_user_model.objects.create(**user_fields(django_user_model, EMAIL))
    try:
        with transaction.atomic():
            django_user_model.objects.create(**user_fields(django_user_model, EMAIL))
    except IntegrityError as exc:
        assert EMAIL in str(exc), "the database quotes the duplicate value"
        try:
            raise SafeMessageError("Sign-up for user failed", code="DUPLICATE") from exc
        except SafeMessageError:
            sentry_sdk.capture_exception()

    (event,) = sentry_events
    values = {value["type"]: value["value"] for value in event["exception"]["values"]}
    assert values["SafeMessageError"] == "Sign-up for user failed"
    assert values["IntegrityError"] == FILTERED
    assert values["UniqueViolation"] == FILTERED
    assert event["tags"]["db.sqlstate"] == "23505"
    table = django_user_model._meta.db_table
    assert event["tags"]["db.table"] == table
    assert event["tags"]["db.constraint"].startswith(f"{table}_")
    assert event["tags"]["error.code"] == "DUPLICATE"
    assert_no_pii(event)


def test_log_records_keep_the_template_and_structured_ids(sentry_events) -> None:
    log = logging.getLogger("taskbeavo.test")
    log.info("Chat message %s from %s", CHAT, NAME)
    log.error(
        "Payout for booking %s to %s failed",
        42,
        IBAN,
        extra={"booking_id": 42, "email": EMAIL, "contact": PHONE, "street_address": ADDRESS},
    )

    (event,) = sentry_events
    assert event["logentry"] == {
        "message": "Payout for booking %s to %s failed",
        "params": [42, FILTERED],
    }
    assert event["extra"]["booking_id"] == 42
    assert event["extra"]["email"] == FILTERED
    (crumb,) = [c for c in event["breadcrumbs"]["values"] if c.get("category") == "taskbeavo.test"]
    assert crumb["message"] == "Chat message %s from %s"
    assert_no_pii(event)


def test_locale_fallback_warning_keeps_recipient_type_id_and_reason(sentry_events) -> None:
    """core.i18n logs only the recipient's type, pk and reason (BEAV-66): all three stay."""

    class User:
        pk = 7
        locale = "xx"

    assert recipient_locale(User()) == "hr"
    sentry_sdk.capture_message("after the fallback")

    (event,) = sentry_events
    (crumb,) = [c for c in event["breadcrumbs"]["values"] if c.get("category") == "core.i18n"]
    assert crumb["level"] == "warning"
    assert crumb["message"] == "Recipient locale %s for %s %s; falling back to %s"
    assert crumb["data"] == {"reason": "unsupported", "recipient_type": "User", "recipient_id": 7}


@pytest.mark.urls("config.tests.telemetry_urls")
@pytest.mark.django_db
def test_crashing_graphql_request_sends_no_personal_data(django_user_model, sentry_events) -> None:
    """A resolver crash through the real Django and Strawberry integrations."""
    user = django_user_model.objects.create(**user_fields(django_user_model, EMAIL))
    token = access_token(user)
    client = Client()
    # A Django session cookie as well, which must be scrubbed like any other.
    client.force_login(user)
    client.cookies["theme"] = NAME

    response = client.post(
        f"/graphql?email={EMAIL}&phone={PHONE}",
        {
            "query": "mutation Crash($t: String!, $o: String!) { crash(chatText: $t, oib: $o) }",
            "variables": {"t": CHAT, "o": OIB},
        },
        content_type="application/json",
        headers={
            "authorization": f"Bearer {token}",
            "x-forwarded-for": "203.0.113.7",
            "x-customer-address": ADDRESS,
            "traceparent": INBOUND_TRACEPARENT,
        },
    )

    assert response.status_code == 200
    assert sentry_events, "the crash must still reach Sentry"
    for event in sentry_events:
        request = event.get("request", {})
        assert set(request) <= {"method", "url", "headers"}
        assert "?" not in request.get("url", "")
        assert {name.lower() for name in request.get("headers", {})} <= {
            "content-type",
            "content-length",
            "host",
            "traceparent",
        }
        assert set(event.get("user", {})) <= {"id"}
        assert "response" not in event.get("contexts", {})
        assert "Bearer" not in json.dumps(event)
        assert token not in json.dumps(event)
        assert "203.0.113.7" not in json.dumps(event)
        assert_no_pii(event)


def test_expected_graphql_errors_are_not_sent() -> None:
    rate_limited = GraphQLError("slow down", extensions={"code": "RATE_LIMITED"})
    invalid_query = GraphQLError("Cannot query field 'x'")
    crash = GraphQLError("boom", original_error=RuntimeError("boom"))

    def send(exc: Exception) -> dict[str, Any] | None:
        return before_send({"level": "error"}, {"exc_info": (type(exc), exc, None)})

    def log(error: GraphQLError) -> dict[str, Any] | None:
        # How Strawberry reports each error of a result: logger.error(error, exc_info=...).
        record = logging.LogRecord("strawberry.execution", logging.ERROR, "", 0, error, (), None)
        return before_send(
            {"level": "error", "logentry": {"message": str(error)}}, {"log_record": record}
        )

    assert send(rate_limited) is None
    assert send(invalid_query) is None
    assert send(crash) is not None
    assert log(rate_limited) is None
    assert log(invalid_query) is None
    assert log(GraphQLError(CHAT, original_error=RuntimeError(CHAT))) == {
        "level": "error",
        "logentry": {"message": "GraphQLError"},
    }


def test_events_are_tagged_with_the_current_trace() -> None:
    with tracer.start_as_current_span("request") as span:
        event = before_send({"level": "error"}, {})
    context = span.get_span_context()

    assert event is not None
    assert event["tags"]["trace_id"] == f"{context.trace_id:032x}"
    assert event["contexts"]["otel"]["span_id"] == f"{context.span_id:016x}"


def test_unknown_event_keys_and_contexts_are_dropped() -> None:
    event = before_send(
        {
            "level": "error",
            "contexts": {"runtime": {"name": "CPython"}, "response": {"data": CHAT}},
            "new_integration_field": {"text": CHAT},
            "extra": {"nested": {"user": {"full_name": NAME}}, "count": 3, "note": EMAIL},
            "tags": {"booking.id": "42", "phone": PHONE},
        },
        {},
    )

    assert event is not None
    assert "new_integration_field" not in event
    assert event["contexts"] == {"runtime": {"name": "CPython"}}
    assert event["extra"] == {"nested": {"user": FILTERED}, "count": 3, "note": FILTERED}
    assert event["tags"] == {"booking.id": "42", "phone": FILTERED}


# Random UUIDs whose digit runs the PII patterns used to match (BEAV-186): a Croatian
# phone number, an OIB and an international phone number.
MANGLED_UUIDS = [
    "05705277-2bb6-4948-9e72-7fd7e18689a9",
    "f8653427-750d-4e24-8812-c00628296766",
    "ef3e0ec1-0034-4260-9d1b-50385a04f524",
]
ID_KEYS = ["booking.id", "task.id", "offer.id", "stripe.object_id"]
# Each ID key trusts one shape (BEAV-187): UUID primary keys a UUID, Stripe keys a
# Stripe ID. Integer primary keys trust none.
UUID_KEYS = ["booking.id", "task.id", "offer.id", "upload.id", "fiscal.receipt_id"]
STRIPE_KEYS = ["stripe.event_id", "stripe.object_id", "stripe.account_id"]
INTEGER_KEYS = ["user.id", "outbox.id", "helper_profile.id"]
STRIPE_ID = "pi_3QxYz1AbCdEfGh2IjKlMnOp"


@pytest.mark.parametrize("uuid", MANGLED_UUIDS)
@pytest.mark.parametrize("key", UUID_KEYS)
def test_whole_uuids_under_a_uuid_key_are_kept(key: str, uuid: str) -> None:
    event = before_send({"tags": {key: uuid}, "extra": {key: uuid}}, {})

    assert event is not None
    assert event["tags"] == {key: uuid}
    assert event["extra"] == {key: uuid}


@pytest.mark.parametrize("uuid", MANGLED_UUIDS)
@pytest.mark.parametrize("key", [*STRIPE_KEYS, *INTEGER_KEYS])
def test_uuids_under_a_stripe_or_integer_key_are_scrubbed_like_any_string(
    key: str, uuid: str
) -> None:
    assert scrub_id(key, uuid) == scrub_text(uuid)


# A UUID-shaped string with an OIB in its last group: kept under a UUID key (trusted by
# shape), filtered under any other ID key.
UUID_WITH_OIB = f"a1b2c3d4-e5f6-4a7b-8c9d-a{OIB}"


@pytest.mark.parametrize("key", [*STRIPE_KEYS, *INTEGER_KEYS])
def test_a_uuid_hiding_an_oib_is_filtered_under_a_stripe_or_integer_key(key: str) -> None:
    assert scrub_id(key, UUID_WITH_OIB) == f"a1b2c3d4-e5f6-4a7b-8c9d-a{FILTERED}"


@pytest.mark.parametrize("key", UUID_KEYS)
def test_a_uuid_hiding_an_oib_is_kept_under_a_uuid_key(key: str) -> None:
    """The case still trusted by shape alone (see `_ID_KEYS`)."""
    assert scrub_id(key, UUID_WITH_OIB) == UUID_WITH_OIB


@pytest.mark.parametrize("key", [*UUID_KEYS, *INTEGER_KEYS])
@pytest.mark.parametrize("value", [STRIPE_ID, "pi_3QxYzAbCdEgx01123456Gx"])
def test_stripe_ids_under_a_non_stripe_key_are_scrubbed_like_any_string(
    key: str, value: str
) -> None:
    assert scrub_id(key, value) == scrub_text(value)


def test_a_stripe_id_hiding_a_phone_number_is_filtered_under_a_uuid_key() -> None:
    # On develop booking.id accepted the Stripe shape, which allows an 8-digit run.
    assert scrub_id("booking.id", "pi_3QxYzAbCdEgx01123456Gx") != "pi_3QxYzAbCdEgx01123456Gx"


@pytest.mark.parametrize(
    "stripe_id",
    [
        "pi_3QxYz1AbCdEfGh2IjKlMnOp",
        "evt_1NG8Du2eZvKYlo2CUI79vXWy",
        "acct_1032D82eZvKYlo2C",
        "cus_NffrFeUfNV2Hib",
        # An 8-digit run is allowed: a random Stripe ID can have one.
        "po_1Ab12345678CdEfGhIjK",
    ],
)
@pytest.mark.parametrize("key", STRIPE_KEYS)
def test_whole_stripe_ids_under_a_stripe_key_are_kept(key: str, stripe_id: str) -> None:
    assert scrub_id(key, stripe_id) == stripe_id


@pytest.mark.parametrize(
    "value",
    [
        f"pi_{OIB}",
        "pi_0912345678",
        f"acct_{OIB}a",
        f"pi_3QxYzAbCd{OIB}EfGh",
        "pi_3QxYzAbCd091234567EfGh",
        "pi_123456789012345678",
        "xx_3QxYz1AbCdEfGh2IjKlMnOp",
        "pi_3QxYz1AbCdEfGh2IjKlMnOp " + OIB,
    ],
)
@pytest.mark.parametrize("key", ["stripe.object_id", "stripe.event_id", "booking.id"])
def test_values_that_only_look_like_stripe_ids_are_not_exempt(key: str, value: str) -> None:
    """They go through the value patterns like any string."""
    assert scrub_id(key, value) == scrub_text(value)


@pytest.mark.parametrize(
    "value", [f"pi_{OIB}", f"acct_{OIB}a", f"pi_3QxYzAbCd{OIB}EfGh", f"pi_ab-{PHONE}"]
)
@pytest.mark.parametrize("key", ["stripe.object_id", "stripe.event_id"])
def test_an_oib_or_phone_dressed_as_a_stripe_id_is_filtered(key: str, value: str) -> None:
    event = before_send({"tags": {key: value}}, {})

    assert event is not None
    assert FILTERED in event["tags"][key]
    assert_no_pii(event)


@pytest.mark.parametrize("key", [*ID_KEYS, "stripe.event_id", "support.reason", "detail"])
@pytest.mark.parametrize(
    "value",
    [
        OIB,
        PHONE,
        "091 234 5678",
        EMAIL,
        IBAN,
        f"{MANGLED_UUIDS[0]} {OIB}",
        f"evt_1Ab {PHONE}",
        f"pi_{OIB}",
        f"pi_{OIB}-x",
        MANGLED_UUIDS[0].upper(),
    ],
)
def test_pii_under_an_id_key_or_any_other_key_is_still_filtered(key: str, value: str) -> None:
    event = before_send({"tags": {key: value}, "extra": {key: value}}, {})

    assert event is not None
    assert FILTERED in event["tags"][key]
    assert FILTERED in event["extra"][key]
    assert_no_pii(event)


# Random hex trace and span IDs whose digit runs the OIB pattern matches (BEAV-186 R1-3).
MANGLED_TRACE_ID = "ab12345678901cdef0123456789abcde"
MANGLED_SPAN_ID = "a12345678901bcde"


def test_whole_trace_and_span_ids_in_contexts_are_kept() -> None:
    trace_context = {
        "trace_id": MANGLED_TRACE_ID,
        "span_id": MANGLED_SPAN_ID,
        "parent_span_id": MANGLED_SPAN_ID,
    }
    event = before_send({"contexts": {"trace": dict(trace_context)}}, {})

    assert event is not None
    assert event["contexts"]["trace"] == trace_context


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("trace_id", OIB),
        ("trace_id", MANGLED_TRACE_ID.upper()),
        ("trace_id", MANGLED_TRACE_ID + "0"),
        ("trace_id", MANGLED_SPAN_ID),
        ("span_id", MANGLED_TRACE_ID),
        ("span_id", f"{MANGLED_SPAN_ID} "),
        ("parent_span_id", f"{OIB}-abcd"),
        ("parent_span_id", PHONE),
        ("trace_id", f"{MANGLED_TRACE_ID[:-1]}g"),
    ],
)
def test_values_that_only_look_like_trace_ids_are_not_exempt(key: str, value: str) -> None:
    event = before_send({"contexts": {"trace": {key: value}}}, {})

    assert event is not None
    assert event["contexts"]["trace"][key] == scrub_text(value)
    assert FILTERED in event["contexts"]["trace"][key]


def test_uuids_are_scrubbed_like_any_string_under_other_keys() -> None:
    event = before_send({"tags": {"support.reason": MANGLED_UUIDS[0]}}, {})

    assert event is not None
    assert event["tags"]["support.reason"] != MANGLED_UUIDS[0]


def test_safe_message_error_ids_keep_whole_uuids(sentry_events) -> None:
    try:
        raise PayoutFailedError(
            "Booking payout failed", ids={"booking.id": MANGLED_UUIDS[1], "task.id": OIB}
        )
    except PayoutFailedError:
        sentry_sdk.capture_exception()

    (event,) = sentry_events
    assert event["tags"]["booking.id"] == MANGLED_UUIDS[1]
    assert event["tags"]["task.id"] == FILTERED


@pytest.mark.parametrize(
    "text",
    [
        f"payout to {IBAN}",
        "payout to HR06 0000 0000 0000 0016 0",
        f"OIB {OIB}",
        f"call {PHONE}",
        "call 00385912345678",
        "call 091 234 5678",
        f"mail {EMAIL}",
    ],
)
def test_pii_patterns_in_free_strings_are_replaced(text: str) -> None:
    crumb = before_breadcrumb({"message": text, "data": {"detail": text}}, None)

    assert FILTERED in crumb["message"]
    assert FILTERED in crumb["data"]["detail"]
    assert_no_pii(crumb)


@pytest.mark.parametrize(
    "text",
    [
        # A phone number right after a letter or an underscore (BEAV-187).
        "ref_0912345678",
        "x0912345678",
        "tel0038591234567",
        "phone_091 234 5678",
        "tel+385 91 234 5678",
        "Call:091 234 5678",
        "a+385 91 234 5678",
        "id-+385912345678",
        # Filtered on develop, so still filtered: after "-", and with hex after a "+" or
        # "00" number.
        "x-0912345678",
        "call-091 234 5678",
        "phone-0912345678",
        "tel-00385912345678",
        "code-00385912345678",
        "+385912345678a",
        "00385912345678abc",
        # After a group of four digits or hex and "-" (BEAV-187 review r1).
        "2026-0912345678",
        "ref 4711-0912345678",
        "1234-00385912345678",
        "Cafe-091 234 5678",
        "dead-0912345678",
    ],
)
def test_phone_numbers_glued_to_a_word_are_replaced(text: str) -> None:
    assert FILTERED in scrub_text(text)
    # Every example's number holds "91 234"; prefixes like "2026-" may stay.
    assert "91234" not in scrub_text(text).replace(" ", "")


# The value patterns on develop, before BEAV-187, for the false-positive baseline.
_DEVELOP_PATTERNS = (
    re.compile(r"[^\s@<>\"']+@[^\s@<>\"']+\.[A-Za-z]{2,}"),
    re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){3,7}(?:[ ]?[A-Z0-9]{1,3})?\b"),
    re.compile(r"(?<![\d.])\d{11}(?![\d.])"),
    re.compile(r"(?<![\w+])(?:\+|00)\d[\d \t/().-]{6,}\d"),
    re.compile(r"(?<!\w)0\d{1,2}[ /-]?\d{3}[ -]?\d{3,4}(?!\w)"),
)


def _develop_scrub_text(text: str) -> str:
    for pattern in _DEVELOP_PATTERNS:
        text = pattern.sub(FILTERED, text)
    return text


@pytest.mark.parametrize(
    ("shape", "bits"),
    [
        ("uuid4", 128),
        ("hex32", 128),
        ("hex40", 160),
    ],
)
def test_random_ids_are_not_filtered_more_often_than_on_develop(shape: str, bits: int) -> None:
    """Seeded sample of random IDs under a non-ID key: the hex-aware phone boundaries
    must not mangle more of them than the develop patterns did."""
    rng = random.Random(f"BEAV-187-{shape}")  # noqa: S311 - a seeded sample, not a secret
    samples = 100_000
    new = develop = 0
    for _ in range(samples):
        value = rng.getrandbits(bits)
        text = str(UUID(int=value, version=4)) if shape == "uuid4" else f"{value:0{bits // 4}x}"
        new += scrub_text(text) != text
        develop += _develop_scrub_text(text) != text

    assert new <= develop


@pytest.mark.parametrize(
    "text", ["booking 42 moved to FUNDS_HELD", "2026-10-28 12:00:00", "outbox:17:push:v1"]
)
def test_ids_dates_and_keys_are_kept(text: str) -> None:
    assert before_breadcrumb({"message": text}, None)["message"] == text


def test_outgoing_http_breadcrumbs_lose_their_query_string() -> None:
    crumb = before_breadcrumb(
        {
            "type": "http",
            "data": {
                "url": f"https://api.stripe.com/v1/customers?email={EMAIL}",
                "http.query": f"email={EMAIL}",
                "method": "GET",
            },
        },
        None,
    )

    assert crumb["data"] == {"url": "https://api.stripe.com/v1/customers", "method": "GET"}
