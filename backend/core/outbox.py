"""Writing and publishing outbox rows (hard rule c).

Writing: `enqueue()` inside the transaction of a state change. It stores the current
`traceparent` and, once the transaction commits, asks Celery to publish the row.

Publishing: `publish_row()` is run by the `core.outbox.publish` task and by the
`core.outbox.sweep` Beat task. How it publishes each row once:

1. It claims the row with `SELECT ... FOR UPDATE SKIP LOCKED`, filtered to
   `status = PENDING` and `next_attempt_at <= now()`. A second worker holding the same
   row ID skips it instead of waiting, and a row already PUBLISHED or FAILED is never
   picked again. The lock is held until the outcome below is committed.
2. It runs the topic's handler inside a savepoint, under a span that continues the
   trace saved in the row.
3. Success sets `PUBLISHED` and `published_at` in the same transaction that holds the
   lock. Failure rolls back the handler's own writes, counts the attempt, records the
   exception class name (never its message) and sets `next_attempt_at` with exponential
   backoff (`OUTBOX_BACKOFF_BASE_SECONDS * 2**(attempts - 1)`, capped at
   `OUTBOX_BACKOFF_MAX_SECONDS`). After `OUTBOX_MAX_ATTEMPTS` the row becomes `FAILED`
   and is logged for a human.
4. If the worker dies after a handler's side effect but before the commit, the lock is
   released, the row is still `PENDING` and the sweep publishes it again. Delivery is
   therefore once per successful commit and at-least-once overall: handlers must be
   idempotent. Each side effect passes `outbox_key(row, channel)` as its idempotency
   key (Expo, Resend) or dedup record, so a repeat is dropped downstream.

A handler runs inside the transaction that holds the row lock, so it must bound every
network call with a timeout. The `core.outbox.publish` task also has a soft time limit
(`OUTBOX_PUBLISH_SOFT_TIME_LIMIT_SECONDS`): a handler that overruns it fails like any
other exception and is retried.

Rows for one aggregate are not ordered. Handlers reload the current object rather than
trusting the order of events.
"""

import enum
import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from core.models import Outbox
from core.payloads import check_ids_only
from core.tracing import context_from_traceparent, current_traceparent

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

Handler = Callable[[Outbox], None]
_handlers: dict[str, Handler] = {}
_CHANNEL = re.compile(r"[a-z][a-z0-9_]{0,31}")


class UnknownTopicError(Exception):
    """No handler is registered for an outbox row's topic."""


class PublishResult(enum.StrEnum):
    PUBLISHED = "published"
    RETRY_SCHEDULED = "retry_scheduled"
    FAILED = "failed"
    # Already published, failed for good, not due yet, or claimed by another worker.
    SKIPPED = "skipped"


def register_handler(topic: str) -> Callable[[Handler], Handler]:
    """Register the function that publishes rows of `topic`. One handler per topic."""

    def decorator(handler: Handler) -> Handler:
        if topic in _handlers:
            raise ValueError(f"An outbox handler for {topic!r} is already registered.")
        _handlers[topic] = handler
        return handler

    return decorator


def enqueue(topic: str, aggregate: models.Model, payload: dict[str, Any] | None = None) -> Outbox:
    """Write an outbox row in the current transaction and publish it after commit.

    Must be called inside `transaction.atomic()` (normally `locked_for_update`), so the
    row commits or rolls back with the change it describes. `payload` holds IDs only.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("enqueue() must run inside the transaction of the change.")
    payload = payload or {}
    check_ids_only(payload, "payload")
    traceparent, tracestate = current_traceparent()
    row = Outbox.objects.create(
        topic=topic,
        aggregate_type=aggregate._meta.label,
        aggregate_id=str(aggregate.pk),
        payload=payload,
        traceparent=traceparent,
        tracestate=tracestate,
    )
    # core.tasks imports this module, so the task is looked up at call time.
    from core.tasks import publish_outbox

    transaction.on_commit(lambda: publish_outbox.delay(row.pk))
    return row


def outbox_key(row: Outbox, channel: str) -> str:
    """Stable dedup key for one side effect of a row, e.g. `outbox:17:push:v1`.

    The same row and channel give the same key on every retry and redelivery. Bump the
    version only when the side effect itself changes meaning.
    """
    if row.pk is None:
        raise ValueError("The outbox row must be saved before it has a key.")
    if not _CHANNEL.fullmatch(channel):
        raise ValueError(f"Outbox key channel {channel!r} must match {_CHANNEL.pattern}.")
    return f"outbox:{row.pk}:{channel}:v1"


def publish_row(row_id: int) -> tuple[PublishResult, datetime | None]:
    """Publish one row if it is due and nobody else holds it.

    Returns the result and, for `RETRY_SCHEDULED`, when the next attempt is due.
    """
    with transaction.atomic():
        row = (
            Outbox.objects.select_for_update(skip_locked=True)
            .filter(pk=row_id, status=Outbox.Status.PENDING, next_attempt_at__lte=timezone.now())
            .first()
        )
        if row is None:
            return PublishResult.SKIPPED, None

        parent = context_from_traceparent(row.traceparent, row.tracestate)
        with tracer.start_as_current_span(
            "outbox.publish", context=parent, kind=SpanKind.CONSUMER
        ) as span:
            span.set_attribute("outbox.id", str(row.pk))
            span.set_attribute("notification.topic", row.topic)
            row.attempts += 1
            try:
                with transaction.atomic():
                    handler = _handlers.get(row.topic)
                    if handler is None:
                        raise UnknownTopicError
                    handler(row)
            except Exception as exc:
                return _record_failure(row, exc, span)

            row.status = Outbox.Status.PUBLISHED
            row.published_at = timezone.now()
            row.last_error = ""
            row.save(update_fields=["status", "published_at", "attempts", "last_error"])
            return PublishResult.PUBLISHED, None


def due_row_ids(limit: int = 500) -> list[int]:
    """IDs of pending rows whose next attempt is due, oldest first."""
    return list(
        Outbox.objects.filter(status=Outbox.Status.PENDING, next_attempt_at__lte=timezone.now())
        .order_by("id")
        .values_list("id", flat=True)[:limit]
    )


def backoff(attempts: int) -> timedelta:
    """Delay before the next attempt after `attempts` failures."""
    seconds = settings.OUTBOX_BACKOFF_BASE_SECONDS * 2 ** (attempts - 1)
    return timedelta(seconds=min(seconds, settings.OUTBOX_BACKOFF_MAX_SECONDS))


def _record_failure(
    row: Outbox, exc: Exception, span: trace.Span
) -> tuple[PublishResult, datetime | None]:
    # The exception message can carry personal data, so only its class name is kept.
    error = type(exc).__qualname__[:200]
    span.set_status(Status(StatusCode.ERROR, error))
    row.last_error = error
    if row.attempts >= settings.OUTBOX_MAX_ATTEMPTS:
        row.status = Outbox.Status.FAILED
        row.save(update_fields=["status", "attempts", "last_error"])
        logger.error(
            "Outbox row %s (%s) failed for good after %s attempts: %s",
            row.pk,
            row.topic,
            row.attempts,
            error,
        )
        return PublishResult.FAILED, None
    row.next_attempt_at = timezone.now() + backoff(row.attempts)
    row.save(update_fields=["attempts", "last_error", "next_attempt_at"])
    logger.warning(
        "Outbox row %s (%s) attempt %s failed: %s", row.pk, row.topic, row.attempts, error
    )
    return PublishResult.RETRY_SCHEDULED, row.next_attempt_at
