import logging
import threading
import time
from contextvars import Token
from datetime import timedelta
from unittest import mock

import pytest
from django.db import connection, transaction
from django.utils import timezone
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from accounts.tests.factories import make_user
from core import outbox
from core.models import Outbox
from core.outbox import PublishResult, enqueue, outbox_key, publish_row
from core.tasks import publish_outbox, sweep_outbox

pytestmark = pytest.mark.django_db
tracer = trace.get_tracer(__name__)


class HandlerError(Exception):
    pass


@pytest.fixture
def calls(monkeypatch) -> list[int]:
    """Register handlers for the test topics and record each row they publish."""
    published: list[int] = []

    def ok(row: Outbox) -> None:
        published.append(row.pk)

    def fails(row: Outbox) -> None:
        # A write the savepoint must roll back, and a message that must not be logged.
        Outbox.objects.filter(pk=row.pk).update(topic="changed.by.handler")
        raise HandlerError("Mama Ana, Ulica 1, +385 91 000 0000")

    monkeypatch.setitem(outbox._handlers, "test.ok", ok)
    monkeypatch.setitem(outbox._handlers, "test.fails", fails)
    return published


def make_row(topic: str = "test.ok", **fields) -> Outbox:
    return Outbox.objects.create(
        topic=topic, aggregate_type="core.Test", aggregate_id="1", **fields
    )


def test_enqueue_refuses_to_run_outside_a_transaction(django_user_model) -> None:
    user = make_user("u1")

    # The test's own transaction is not the change's; faking autocommit shows the misuse.
    with (
        pytest.raises(RuntimeError, match="inside the transaction"),
        mock.patch.object(connection, "in_atomic_block", False),
    ):
        enqueue("test.ok", user)


@pytest.mark.parametrize(
    "payload",
    [
        {"full_name": "Ana"},
        {"phoneNumber": "+385"},
        {"nested": {"email": "a@example.com"}},
        {"items": [{"address": "x"}]},
        {"when": timezone.now()},
        {"gate_code": "1234"},
        {"doorbell": "x"},
        {"postcode": "20000"},
        {"chat_id": 1},
        {"related_id": "ana@example.com"},
    ],
)
def test_enqueue_rejects_payloads_that_are_not_ids(django_user_model, payload) -> None:
    user = make_user("u1")

    with pytest.raises(ValueError), transaction.atomic():
        enqueue("test.ok", user, payload)


def test_enqueue_accepts_ids_enum_keys_and_amounts(django_user_model, calls) -> None:
    user = make_user("u1")

    with transaction.atomic():
        row = enqueue("test.ok", user, {"booking_id": 7, "related_id": "x", "status": "FUNDS_HELD"})

    assert row.aggregate_type == "accounts.User"
    assert row.aggregate_id == str(user.pk)


def test_enqueued_row_is_published_once_after_commit(
    django_user_model, calls, django_capture_on_commit_callbacks
) -> None:
    user = make_user("u1")

    with django_capture_on_commit_callbacks(execute=True), transaction.atomic():
        row = enqueue("test.ok", user, {"user_id": user.pk})
        assert calls == []

    row.refresh_from_db()
    assert calls == [row.pk]
    assert row.status == Outbox.Status.PUBLISHED
    assert row.attempts == 1
    assert row.published_at is not None


def test_rolled_back_change_leaves_no_row_and_publishes_nothing(
    django_user_model, calls, django_capture_on_commit_callbacks
) -> None:
    user = make_user("u1")

    with (
        django_capture_on_commit_callbacks(execute=True) as callbacks,
        pytest.raises(HandlerError),
        transaction.atomic(),
    ):
        enqueue("test.ok", user)
        raise HandlerError

    assert callbacks == []
    assert not Outbox.objects.exists()
    assert calls == []


def test_publishing_a_published_row_again_is_a_no_op(calls) -> None:
    row = make_row()

    assert publish_row(row.pk) == (PublishResult.PUBLISHED, None)
    assert publish_row(row.pk) == (PublishResult.SKIPPED, None)
    assert publish_outbox(row.pk) == "skipped"
    assert calls == [row.pk]


def test_failed_publish_is_retried_with_backoff_and_rolls_back_handler_writes(
    calls, settings
) -> None:
    settings.OUTBOX_BACKOFF_BASE_SECONDS = 30
    row = make_row("test.fails")
    before = timezone.now()

    result, retry_at = publish_row(row.pk)

    row.refresh_from_db()
    assert result == PublishResult.RETRY_SCHEDULED
    assert row.status == Outbox.Status.PENDING
    assert row.attempts == 1
    assert row.topic == "test.fails"
    assert row.last_error == "HandlerError"
    assert retry_at == row.next_attempt_at
    assert before + timedelta(seconds=30) <= row.next_attempt_at
    assert row.next_attempt_at <= timezone.now() + timedelta(seconds=30)
    # Not due yet: a second worker or an early retry message does nothing.
    assert publish_row(row.pk) == (PublishResult.SKIPPED, None)


def test_retry_publishes_once_the_handler_recovers(calls) -> None:
    row = make_row("test.fails")
    publish_row(row.pk)
    Outbox.objects.filter(pk=row.pk).update(topic="test.ok", next_attempt_at=timezone.now())

    assert publish_row(row.pk) == (PublishResult.PUBLISHED, None)

    row.refresh_from_db()
    assert row.attempts == 2
    assert row.last_error == ""
    assert calls == [row.pk]


def test_row_fails_for_good_after_max_attempts_and_the_sweep_leaves_it(calls, settings) -> None:
    settings.OUTBOX_MAX_ATTEMPTS = 2
    row = make_row("test.fails")

    assert publish_row(row.pk)[0] == PublishResult.RETRY_SCHEDULED
    Outbox.objects.filter(pk=row.pk).update(next_attempt_at=timezone.now())
    assert publish_row(row.pk) == (PublishResult.FAILED, None)

    row.refresh_from_db()
    assert row.status == Outbox.Status.FAILED
    assert row.attempts == 2
    assert outbox.due_row_ids() == []


def test_unknown_topic_is_retried_not_dropped(calls) -> None:
    row = make_row("test.nobody")

    assert publish_row(row.pk)[0] == PublishResult.RETRY_SCHEDULED
    row.refresh_from_db()
    assert row.last_error == "UnknownTopicError"


def test_failure_logs_never_contain_the_exception_message(calls, caplog) -> None:
    row = make_row("test.fails")

    with caplog.at_level(logging.INFO, logger="core.outbox"):
        publish_row(row.pk)

    assert "HandlerError" in caplog.text
    assert "Mama Ana" not in caplog.text
    assert "+385" not in caplog.text


def test_backoff_doubles_and_is_capped(settings) -> None:
    settings.OUTBOX_BACKOFF_BASE_SECONDS = 30
    settings.OUTBOX_BACKOFF_MAX_SECONDS = 3600

    assert [outbox.backoff(n).total_seconds() for n in (1, 2, 3, 7, 8, 20)] == [
        30,
        60,
        120,
        1920,
        3600,
        3600,
    ]


def test_publish_task_schedules_the_retry_it_was_given(calls) -> None:
    row = make_row("test.fails")

    with mock.patch.object(publish_outbox, "apply_async") as apply_async:
        assert publish_outbox(row.pk) == "retry_scheduled"

    row.refresh_from_db()
    apply_async.assert_called_once_with((row.pk,), eta=row.next_attempt_at)


def test_sweep_publishes_due_rows_only(calls) -> None:
    due = make_row()
    later = make_row(next_attempt_at=timezone.now() + timedelta(minutes=5))
    done = make_row(status=Outbox.Status.PUBLISHED)

    assert sweep_outbox() == 1

    assert calls == [due.pk]
    later.refresh_from_db()
    done.refresh_from_db()
    assert later.status == Outbox.Status.PENDING
    assert later.attempts == 0
    assert done.attempts == 0


def test_enqueue_saves_the_trace_and_the_publisher_joins_it(
    django_user_model, calls, spans
) -> None:
    user = make_user("u1")

    with tracer.start_as_current_span("graphql.request") as request_span, transaction.atomic():
        row = enqueue("test.ok", user)
    request = request_span.get_span_context()

    assert row.traceparent == (
        f"00-{request.trace_id:032x}-{request.span_id:016x}-{request.trace_flags:02x}"
    )

    publish_row(row.pk)

    (publish_span,) = [s for s in spans.get_finished_spans() if s.name == "outbox.publish"]
    assert publish_span.context.trace_id == request.trace_id
    assert publish_span.parent is not None
    assert publish_span.parent.span_id == request.span_id
    assert dict(publish_span.attributes or {}) == {
        "outbox.id": str(row.pk),
        "notification.topic": "test.ok",
    }


def _attach_inbound(traceparent: str, tracestate: str) -> Token[Context]:
    """Make the context look like a request whose headers carried this trace context."""
    carrier = {"traceparent": traceparent, "tracestate": tracestate}
    return otel_context.attach(TraceContextTextMapPropagator().extract(carrier))


INBOUND_TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


def test_oversized_inbound_tracestate_does_not_fail_the_change(django_user_model) -> None:
    user = make_user("u1")
    # 32 members of 200 characters: valid W3C, about 6.5k characters on the wire.
    members = [f"v{i}={'a' * 197}" for i in range(32)]
    token = _attach_inbound(INBOUND_TRACEPARENT, ",".join(members))
    try:
        with transaction.atomic():
            row = enqueue("test.ok", user)
    finally:
        otel_context.detach(token)

    row.refresh_from_db()
    assert row.traceparent == INBOUND_TRACEPARENT
    # Only whole leading members are kept, as many as fit the column.
    assert row.tracestate == ",".join(members[:2])
    assert len(row.tracestate) <= Outbox._meta.get_field("tracestate").max_length


def test_tracestate_whose_first_member_is_too_long_is_dropped(django_user_model) -> None:
    user = make_user("u1")
    # Not valid W3C (values are capped at 256), so the propagator may drop it itself;
    # either way nothing longer than the column reaches the insert.
    token = _attach_inbound(INBOUND_TRACEPARENT, "v=" + "a" * 600)
    try:
        with transaction.atomic():
            row = enqueue("test.ok", user)
    finally:
        otel_context.detach(token)

    assert row.traceparent == INBOUND_TRACEPARENT
    assert row.tracestate == ""


def test_outbox_key_is_stable_per_row_and_channel(calls) -> None:
    row = make_row()

    assert outbox_key(row, "push") == f"outbox:{row.pk}:push:v1"
    assert outbox_key(Outbox.objects.get(pk=row.pk), "push") == outbox_key(row, "push")
    assert outbox_key(row, "email") != outbox_key(row, "push")


@pytest.mark.parametrize("channel", ["", "Push", "push:1", "x" * 33])
def test_outbox_key_rejects_bad_channels(channel) -> None:
    with pytest.raises(ValueError):
        outbox_key(make_row(), channel)


def test_outbox_key_needs_a_saved_row() -> None:
    with pytest.raises(ValueError):
        outbox_key(Outbox(topic="test.ok"), "push")


def test_publish_task_has_a_time_limit(settings) -> None:
    soft, hard = publish_outbox.soft_time_limit, publish_outbox.time_limit
    assert soft == settings.OUTBOX_PUBLISH_SOFT_TIME_LIMIT_SECONDS
    assert hard == settings.OUTBOX_PUBLISH_SOFT_TIME_LIMIT_SECONDS + 30


def test_row_without_trace_context_starts_a_new_trace(calls, spans) -> None:
    row = make_row(traceparent="not-a-traceparent")

    assert publish_row(row.pk)[0] == PublishResult.PUBLISHED
    (publish_span,) = spans.get_finished_spans()
    assert publish_span.parent is None


@pytest.mark.django_db(transaction=True)
def test_concurrent_publishers_publish_a_row_once(monkeypatch) -> None:
    published: list[int] = []
    started = threading.Event()

    def slow(row: Outbox) -> None:
        started.set()
        time.sleep(0.5)
        published.append(row.pk)

    monkeypatch.setitem(outbox._handlers, "test.slow", slow)
    row = make_row("test.slow")
    results: list[PublishResult] = []

    def worker() -> None:
        try:
            results.append(publish_row(row.pk)[0])
        finally:
            connection.close()

    first = threading.Thread(target=worker)
    first.start()
    assert started.wait(5)
    second = threading.Thread(target=worker)
    second.start()
    first.join()
    second.join()

    assert published == [row.pk]
    assert sorted(results) == [PublishResult.PUBLISHED, PublishResult.SKIPPED]
