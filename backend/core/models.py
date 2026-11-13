"""Reliability primitives shared by every app: the outbox and the audit-event base model.

Hard rule c: a state change runs in one transaction that locks the row
(`core.transitions.locked_for_update`), writes an audit row (a concrete `AuditEvent`
subclass such as `booking_events`) and an outbox row (`core.outbox.enqueue`).
"""

from typing import Any, ClassVar

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.payloads import check_ids_only
from core.tracing import TRACEPARENT_MAX_LENGTH, TRACESTATE_MAX_LENGTH, current_traceparent


class Outbox(models.Model):
    """A side effect waiting to be published: a subscription event, push, email or tax job.

    Rows are written in the same transaction as the change they describe, so a row
    exists if and only if the change committed. `core.outbox.publish_row` publishes
    each row once and retries it with backoff on failure. `payload` holds IDs only;
    handlers reload the current objects. Never log the payload.
    """

    class Status(models.TextChoices):
        PENDING = "PENDING"
        PUBLISHED = "PUBLISHED"
        # Gave up after OUTBOX_MAX_ATTEMPTS. Needs a human; the sweep ignores it.
        FAILED = "FAILED"

    topic = models.CharField(max_length=100)
    aggregate_type = models.CharField(max_length=50)
    aggregate_id = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)
    # W3C trace context of the request that wrote the row, so Celery joins its trace.
    traceparent = models.CharField(max_length=TRACEPARENT_MAX_LENGTH, blank=True)
    tracestate = models.CharField(max_length=TRACESTATE_MAX_LENGTH, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    # Exception class name only: messages can carry personal data.
    last_error = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "outbox"
        indexes: ClassVar = [
            models.Index(
                fields=["next_attempt_at"],
                name="outbox_pending_due_idx",
                condition=models.Q(status="PENDING"),
            ),
            models.Index(fields=["aggregate_type", "aggregate_id"], name="outbox_aggregate_idx"),
        ]

    def __str__(self) -> str:
        return f"outbox:{self.pk} {self.topic} {self.status}"


class ActorKind(models.TextChoices):
    """Who caused an audited change."""

    POSTER = "POSTER"
    HELPER = "HELPER"
    ADMIN = "ADMIN"
    STRIPE_WEBHOOK = "STRIPE_WEBHOOK"
    SYSTEM = "SYSTEM"


class AuditEventError(Exception):
    """An audit row was about to be changed or deleted."""


class AuditEvent(models.Model):
    """Base for append-only audit tables such as `booking_events`.

    A concrete subclass adds a foreign key to the audited object, for example
    `booking = models.ForeignKey(Booking, on_delete=models.PROTECT, related_name="events")`,
    and is written in the same transaction as the change (hard rule c).

    Rows are append-only: `save()` refuses updates and `delete()` refuses deletes.
    `data` holds IDs, enum keys and amounts in cents, never personal data. GDPR
    deletion anonymises the actor through `on_delete=SET_NULL`.
    """

    type = models.CharField(max_length=50)
    actor_kind = models.CharField(max_length=20, choices=ActorKind.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    data = models.JSONField(default=dict, blank=True)
    traceparent = models.CharField(max_length=TRACEPARENT_MAX_LENGTH, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        abstract = True
        ordering = ("created_at", "id")

    def __str__(self) -> str:
        return f"{type(self).__name__}:{self.pk} {self.type}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise AuditEventError(f"{type(self).__name__} rows are append-only.")
        check_ids_only(self.data, "data")
        if not self.traceparent:
            self.traceparent = current_traceparent()[0]
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> Any:
        raise AuditEventError(f"{type(self).__name__} rows are append-only.")
