"""Helpers' priced offers on open tasks (docs/PLAN.md, "Posting a task and handling offers").

An offer is SENT by a helper who can send offers, lasts 7 days (never past the task's
date) and can be renewed. A big change to the task marks it NEEDS_UPDATE until the helper
confirms or changes it. It ends WITHDRAWN by the helper or EXPIRED. TaskBeavo withdraws a
private helper's offer when the task's poster becomes a business poster
(`withdrawn_reason`, accounts.business).

When the poster accepts one (`bookings.state_machine.create_booking`), it becomes ACCEPTED
and the task's other live offers DECLINED, in the same transaction. Neither expires, can
be withdrawn or renewed, or is marked NEEDS_UPDATE. If that checkout fails or is
abandoned (booking PAYMENT_FAILED), both go back to what they were (SENT or NEEDS_UPDATE),
or EXPIRED if their expiry passed meanwhile.

Personal data: `message` is the helper's text, after the contact-detail filter. The
poster of the task and the helper see it. Deleting the helper deletes their offers
(CASCADE); `offers.gdpr` holds the export.
"""

import uuid
from typing import ClassVar

from django.conf import settings
from django.db import models

from core.models import AuditEvent
from tasks.models import Task


class Offer(models.Model):
    class Status(models.TextChoices):
        SENT = "SENT"
        # The poster changed the task's date, address or scope; the helper confirms or
        # changes the offer (`update_offer`).
        NEEDS_UPDATE = "NEEDS_UPDATE"
        WITHDRAWN = "WITHDRAWN"
        EXPIRED = "EXPIRED"
        # The poster accepted it: a booking exists (bookings.models.Booking).
        ACCEPTED = "ACCEPTED"
        # The poster accepted another offer on the task.
        DECLINED = "DECLINED"

    class WithdrawnReason(models.TextChoices):
        # The task's poster became a business poster, whose tasks take registered helpers
        # only (accounts.business). Neutral: it says nothing about the helper (rule k).
        BUSINESS_POSTER_REGISTERED_ONLY = "BUSINESS_POSTER_REGISTERED_ONLY"

    LIVE: ClassVar = (Status.SENT, Status.NEEDS_UPDATE)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="offers")
    helper = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="offers"
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SENT)
    price_cents = models.PositiveIntegerField()
    # As stored and shown: the contact-detail filter's output (masked where it matched).
    message = models.TextField(blank=True)
    moderation_verdict = models.CharField(max_length=20, blank=True)
    expires_at = models.DateTimeField()
    # When the current lifetime began: sending or the last renewal.
    lifetime_started_at = models.DateTimeField()
    # The poster changed the task's date, address or scope since the helper last sent or
    # confirmed the offer. NEEDS_UPDATE always has it; an EXPIRED offer keeps it, so
    # renewing brings it back as NEEDS_UPDATE instead of SENT.
    task_changed = models.BooleanField(default=False)
    # When the poster's "expires within a day" reminder was queued; renewing clears it.
    expiry_reminder_queued_at = models.DateTimeField(null=True, blank=True)
    # Why TaskBeavo withdrew it; blank when the helper withdrew it or it isn't withdrawn.
    withdrawn_reason = models.CharField(max_length=40, choices=WithdrawnReason.choices, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "offers"
        ordering: ClassVar = ["-created_at"]
        constraints: ClassVar = [
            # One live offer per helper and task; a withdrawn or expired one may be followed
            # by a new one.
            models.UniqueConstraint(
                fields=["task", "helper"],
                condition=models.Q(status__in=["SENT", "NEEDS_UPDATE"]),
                name="offer_one_live_per_helper",
            ),
        ]
        indexes: ClassVar = [
            models.Index(fields=["status", "expires_at"], name="offer_status_expiry"),
            models.Index(fields=["helper", "status"], name="offer_helper_status"),
        ]

    def __str__(self) -> str:
        # No message: __str__ ends up in logs and admin history.
        return f"Offer {self.pk}"

    @property
    def is_live(self) -> bool:
        return self.status in self.LIVE


class OfferEvent(AuditEvent):
    """Audit trail of an offer (hard rule c pattern). `data` holds IDs and cents only."""

    class Type(models.TextChoices):
        SENT = "sent"
        UPDATED = "updated"
        RENEWED = "renewed"
        NEEDS_UPDATE = "needs_update"
        WITHDRAWN = "withdrawn"
        EXPIRED = "expired"
        ACCEPTED = "accepted"
        DECLINED = "declined"
        # The checkout of the booking that accepted or declined it failed.
        REOPENED = "reopened"

    offer = models.ForeignKey(Offer, on_delete=models.CASCADE, related_name="events")

    class Meta(AuditEvent.Meta):
        db_table = "offer_events"
