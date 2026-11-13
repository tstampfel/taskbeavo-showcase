"""Sending one delivery (push or email) and processing push receipts.

`deliver(delivery_id)` is run by the `notifications.send` task and the
`notifications.sweep` Beat task. Like the outbox publisher (core/outbox.py), it claims the
delivery with `SELECT ... FOR UPDATE SKIP LOCKED`, only while it is PENDING and due, and
records the outcome in the same transaction:

- success sets SENT (or SKIPPED when there is nothing to send to);
- an exception counts the attempt, keeps only its class name and schedules a retry with
  `core.outbox.backoff`; after `NOTIFICATION_MAX_ATTEMPTS` the delivery is FAILED.

Text is rendered in the recipient's language (core.i18n), never the sender's.

Email passes `outbox:{row}:email:v1:user:{id}` as Resend's idempotency key, so a resend
after a crash is dropped by Resend. Expo has no idempotency key: if the worker dies
after Expo accepted a push but before the commit, that push can arrive twice.

Push tokens: only live ones are used (notifications/devices.py); dead ones are deleted
first. An `ok` ticket is kept until its receipt is checked (`check_receipts`).
`DeviceNotRegistered`, on a ticket or a receipt, deletes the token. A push of a
NO_PUSH_DEVICE kind that finds no device adds the email fallback.
"""

import enum
import logging
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from core.email import render_email, send_email
from core.i18n import in_recipient_locale
from core.outbox import backoff, outbox_key
from core.tracing import context_from_traceparent
from notifications import expo
from notifications.devices import delete_dead_tokens, live_tokens_of
from notifications.dispatch import add_email_fallback
from notifications.emails import custom_email
from notifications.kinds import KINDS, Kind
from notifications.models import Channel, Delivery, PushTicket, PushToken

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


class DeliveryResult(enum.StrEnum):
    SENT = "sent"
    SKIPPED = "skipped"
    FAILED = "failed"
    RETRY_SCHEDULED = "retry_scheduled"
    # Already done, not due yet, or claimed by another worker.
    NOT_DUE = "not_due"


class AllTicketsFailedError(Exception):
    """Expo returned an error ticket for every device."""


def deliver(delivery_id: int) -> tuple[DeliveryResult, datetime | None]:
    """Send one delivery if it is due and nobody else holds it.

    Returns the result and, for RETRY_SCHEDULED, when the next attempt is due.
    """
    with transaction.atomic():
        delivery = (
            Delivery.objects.select_for_update(skip_locked=True, of=("self",))
            .select_related("outbox", "user")
            .filter(
                pk=delivery_id,
                status=Delivery.Status.PENDING,
                next_attempt_at__lte=timezone.now(),
            )
            .first()
        )
        if delivery is None:
            return DeliveryResult.NOT_DUE, None

        row = delivery.outbox
        parent = context_from_traceparent(row.traceparent, row.tracestate)
        with tracer.start_as_current_span("notification.deliver", context=parent) as span:
            span.set_attribute("outbox.id", str(row.pk))
            span.set_attribute("notification.topic", row.topic)
            span.set_attribute("notification.kind", delivery.kind)
            span.set_attribute("notification.channel", delivery.channel)
            span.set_attribute("user.id", str(delivery.user_id))
            delivery.attempts += 1
            try:
                with transaction.atomic():
                    kind = KINDS[delivery.kind]
                    if not delivery.user.is_active:
                        # Deactivated (suspended or deleting) since the event.
                        status = Delivery.Status.SKIPPED
                    elif delivery.channel == Channel.PUSH:
                        status = _send_push(delivery, kind)
                    else:
                        status = _send_email(delivery, kind)
            except Exception as exc:
                return _record_failure(delivery, exc, span)

            delivery.status = status
            delivery.sent_at = timezone.now() if status == Delivery.Status.SENT else None
            delivery.last_error = ""
            delivery.save(update_fields=["status", "sent_at", "attempts", "last_error"])
            span.set_attribute("notification.status", status)
            return DeliveryResult(status.lower()), None


def due_delivery_ids(limit: int = 500) -> list[int]:
    """IDs of pending deliveries whose next attempt is due, oldest first."""
    return list(
        Delivery.objects.filter(status=Delivery.Status.PENDING, next_attempt_at__lte=timezone.now())
        .order_by("id")
        .values_list("id", flat=True)[:limit]
    )


def purge_finished() -> int:
    """Delete finished deliveries past their retention, and dead push tokens; returns how many.

    They only prove a message went out, and name who got it (GDPR data minimisation).
    Pending ones are kept until they are sent or fail.
    """
    cutoff = timezone.now() - timedelta(days=settings.NOTIFICATION_DELIVERY_RETENTION_DAYS)
    deleted, _ = (
        Delivery.objects.filter(created_at__lt=cutoff)
        .exclude(status=Delivery.Status.PENDING)
        .delete()
    )
    return deleted + delete_dead_tokens()


def check_receipts() -> int:
    """Fetch the receipts of tickets old enough to have one; returns tokens deleted.

    Expo keeps receipts for about a day; tickets older than that are dropped unchecked.
    """
    now = timezone.now()
    PushTicket.objects.filter(
        created_at__lt=now - timedelta(hours=settings.PUSH_RECEIPT_MAX_AGE_HOURS)
    ).delete()
    tickets = list(
        PushTicket.objects.filter(
            created_at__lte=now - timedelta(minutes=settings.PUSH_RECEIPT_DELAY_MINUTES)
        )
        .order_by("id")
        .values_list("ticket_id", "push_token_id")[: settings.PUSH_RECEIPT_BATCH]
    )
    if not tickets:
        return 0
    with tracer.start_as_current_span("notification.push_receipts") as span:
        span.set_attribute("notification.channel", Channel.PUSH)
        found = expo.receipts([ticket_id for ticket_id, _ in tickets])
        dead_tokens = {
            token_id
            for ticket_id, token_id in tickets
            if (receipt := found.get(ticket_id)) is not None
            and receipt.error == expo.DEVICE_NOT_REGISTERED
        }
        errors = {receipt.error or "UnknownError" for receipt in found.values() if not receipt.ok}
        other_errors = sorted(errors - {expo.DEVICE_NOT_REGISTERED})
        if other_errors:
            logger.warning("Push receipt errors: %s", ",".join(other_errors))
        # Deleting a token deletes its tickets; the others with a receipt are done.
        PushToken.objects.filter(pk__in=dead_tokens).delete()
        PushTicket.objects.filter(ticket_id__in=list(found)).delete()
        return len(dead_tokens)


def _send_push(delivery: Delivery, kind: Kind) -> str:
    delete_dead_tokens(delivery.user_id)
    tokens = list(live_tokens_of(delivery.user_id).order_by("pk"))
    if not tokens:
        add_email_fallback(delivery)
        return Delivery.Status.SKIPPED
    route = kind.route_for(delivery.outbox)
    with in_recipient_locale(delivery.user):
        title, body = str(kind.title), str(kind.body)
    messages = [
        expo.PushMessage(to=token.token, title=title, body=body, data={"route": route})
        for token in tokens
    ]
    tickets = expo.send(messages)
    dead: list[int] = []
    errors: list[str] = []
    for token, ticket in zip(tokens, tickets, strict=True):
        if ticket.ok:
            if ticket.id:
                PushTicket.objects.create(delivery=delivery, push_token=token, ticket_id=ticket.id)
        elif ticket.error == expo.DEVICE_NOT_REGISTERED:
            dead.append(token.pk)
        else:
            errors.append(ticket.error or "UnknownError")
    if dead:
        PushToken.objects.filter(pk__in=dead).delete()
    if errors:
        logger.warning("Push delivery %s ticket errors: %s", delivery.pk, ",".join(errors))
    if any(ticket.ok for ticket in tickets):
        return Delivery.Status.SENT
    if errors:
        # e.g. MessageRateExceeded: worth another attempt.
        raise AllTicketsFailedError(errors[0])
    # Every device turned out dead.
    add_email_fallback(delivery)
    return Delivery.Status.SKIPPED


def _send_email(delivery: Delivery, kind: Kind) -> str:
    user = delivery.user
    key = f"{outbox_key(delivery.outbox, 'email')}:user:{delivery.user_id}"
    custom = custom_email(kind.key)
    if custom is not None:
        rendered = custom.render(delivery)
        message_id = send_email(
            rendered.to or user.email,
            rendered.email,
            idempotency_key=key,
            attachments=rendered.attachments,
        )
        if custom.on_sent is not None:
            custom.on_sent(delivery, rendered, message_id)
        return Delivery.Status.SENT
    route = kind.route_for(delivery.outbox)
    with in_recipient_locale(user) as locale:
        link = f"{settings.WEB_APP_URL}/{locale}{route}"
    email = render_email(
        "notification", user, {"title": kind.title, "body": kind.body, "link": link}
    )
    send_email(user.email, email, idempotency_key=key)
    return Delivery.Status.SENT


def _record_failure(
    delivery: Delivery, exc: Exception, span: trace.Span
) -> tuple[DeliveryResult, datetime | None]:
    # Exception messages can carry personal data (an address in an SMTP error), so only
    # the class name is kept.
    error = type(exc).__qualname__[:200]
    span.set_status(Status(StatusCode.ERROR, error))
    delivery.last_error = error
    custom = custom_email(delivery.kind) if delivery.channel == Channel.EMAIL else None
    final = delivery.attempts == settings.NOTIFICATION_MAX_ATTEMPTS
    if final and custom is not None and custom.on_failed is not None:
        custom.on_failed(delivery)
    if delivery.attempts >= settings.NOTIFICATION_MAX_ATTEMPTS:
        slow = custom.slow_retry if custom is not None else None
        if slow is not None and timezone.now() + slow.every <= delivery.created_at + slow.for_:
            # A confirmation the law requires: keep trying, slowly (review R3-1).
            delivery.next_attempt_at = timezone.now() + slow.every
            delivery.save(update_fields=["attempts", "last_error", "next_attempt_at"])
            logger.warning(
                "Delivery %s (%s, %s) attempt %s failed, retrying slowly: %s",
                delivery.pk,
                delivery.kind,
                delivery.channel,
                delivery.attempts,
                error,
            )
            return DeliveryResult.RETRY_SCHEDULED, delivery.next_attempt_at
        delivery.status = Delivery.Status.FAILED
        delivery.save(update_fields=["status", "attempts", "last_error"])
        logger.error(
            "Delivery %s (%s, %s) failed for good after %s attempts: %s",
            delivery.pk,
            delivery.kind,
            delivery.channel,
            delivery.attempts,
            error,
        )
        return DeliveryResult.FAILED, None
    delivery.next_attempt_at = timezone.now() + backoff(delivery.attempts)
    delivery.save(update_fields=["attempts", "last_error", "next_attempt_at"])
    logger.warning(
        "Delivery %s (%s, %s) attempt %s failed: %s",
        delivery.pk,
        delivery.kind,
        delivery.channel,
        delivery.attempts,
        error,
    )
    return DeliveryResult.RETRY_SCHEDULED, delivery.next_attempt_at
