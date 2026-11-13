"""Push tokens, notification preferences and the per-recipient deliveries of outbox rows.

How one outbox row becomes push and email is described in notifications/dispatch.py.
"""

from typing import ClassVar

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import Outbox


class Category(models.TextChoices):
    """What a notification is about. Decides whether the user may switch it off."""

    BOOKING = "BOOKING"
    PAYMENT = "PAYMENT"
    OFFERS = "OFFERS"
    MESSAGES = "MESSAGES"


# Booking and payment messages are part of the service and can't be switched off
# (plan: "Marketing emails and pushes are opt-in").
MANDATORY_CATEGORIES = frozenset({Category.BOOKING, Category.PAYMENT})


class Channel(models.TextChoices):
    PUSH = "push"
    EMAIL = "email"


class PushToken(models.Model):
    """An Expo push token of one signed-in device.

    The token is personal data (it identifies a device): never log it or put it on a span.
    It belongs to the session that registered it and is used only while that session is
    live (notifications/devices.py): sign-out deletes it, and so does Expo's
    `DeviceNotRegistered` (notifications/delivery.py).
    """

    class Platform(models.TextChoices):
        IOS = "ios"
        ANDROID = "android"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="push_tokens"
    )
    user_id: int
    session = models.ForeignKey(
        "accounts.Session", on_delete=models.CASCADE, related_name="push_tokens"
    )
    token = models.CharField(max_length=255, unique=True)
    platform = models.CharField(max_length=10, choices=Platform.choices)
    created_at = models.DateTimeField(default=timezone.now)
    registered_at = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:
        return f"PushToken {self.pk}"


class NotificationPreference(models.Model):
    """A user's choice for one switchable category on one channel.

    Only changes are stored: without a row, the category is on. Rows for mandatory
    categories are never written (notifications/preferences.py).
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_preferences"
    )
    category = models.CharField(max_length=20, choices=Category.choices)
    channel = models.CharField(max_length=10, choices=Channel.choices)
    enabled = models.BooleanField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["user", "category", "channel"], name="notification_preference_unique"
            )
        ]

    def __str__(self) -> str:
        return f"NotificationPreference {self.pk}"


class Delivery(models.Model):
    """One push or email of one outbox row to one user.

    The unique (outbox, user, channel) row is the dedup record: a redelivered outbox row
    finds its deliveries already there and sends nothing twice. Each delivery is sent by
    its own Celery task and retried with backoff, so a failing channel doesn't hold up
    the others.
    """

    class Status(models.TextChoices):
        PENDING = "PENDING"
        SENT = "SENT"
        # Nothing to send to, e.g. no registered device. Not an error.
        SKIPPED = "SKIPPED"
        # Gave up after NOTIFICATION_MAX_ATTEMPTS, or every push ticket was an error.
        FAILED = "FAILED"

    outbox = models.ForeignKey(Outbox, on_delete=models.CASCADE, related_name="deliveries")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_deliveries"
    )
    user_id: int
    # A key of notifications.kinds.KINDS, e.g. "booking.payment_requested".
    kind = models.CharField(max_length=50)
    channel = models.CharField(max_length=10, choices=Channel.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    # An exception class name or Expo error code, never a message.
    last_error = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["outbox", "user", "channel"], name="notification_delivery_unique"
            )
        ]
        indexes: ClassVar = [
            models.Index(
                fields=["next_attempt_at"],
                name="delivery_pending_due_idx",
                condition=models.Q(status="PENDING"),
            )
        ]

    def __str__(self) -> str:
        return f"Delivery {self.pk} {self.channel} {self.status}"


class PushTicket(models.Model):
    """An Expo push ticket waiting for its receipt.

    Expo answers a send with tickets; whether APNs or FCM accepted the message is only
    known from the receipt, fetched a little later (notifications.check_push_receipts).
    A ticket is deleted once its receipt is processed, or after Expo stops keeping it.
    """

    delivery = models.ForeignKey(Delivery, on_delete=models.CASCADE, related_name="push_tickets")
    push_token = models.ForeignKey(PushToken, on_delete=models.CASCADE, related_name="tickets")
    ticket_id = models.CharField(max_length=100, unique=True)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:
        return f"PushTicket {self.pk}"
