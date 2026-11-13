from typing import ClassVar

from django.contrib.postgres.fields import ArrayField
from django.db import models

from core.models import AuditEvent


class StripeEvent(models.Model):
    """Every processed Stripe webhook event, inserted first in the transaction that
    processes it (hard rule a).

    The Stripe event ID is the primary key, so a redelivered event cannot be inserted
    twice. Insert through `payments.events.record_stripe_event`, never `create()`.
    `payload` can hold billing details: never log it or put it in telemetry. It is kept
    in full for `STRIPE_EVENT_PAYLOAD_RETENTION_DAYS`, then trimmed to non-personal
    fields; the row itself is never deleted, so the ID keeps rejecting duplicates.
    """

    id = models.CharField(primary_key=True, max_length=255)
    type = models.CharField(max_length=100)
    livemode = models.BooleanField()
    payload = models.JSONField()
    received_at = models.DateTimeField()
    processed_at = models.DateTimeField(null=True, blank=True)
    payload_trimmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "stripe_events"
        indexes: ClassVar = [
            models.Index(
                fields=["received_at"],
                name="stripe_events_untrimmed_idx",
                condition=models.Q(payload_trimmed_at__isnull=True),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.id} {self.type}"


class ConnectedAccount(models.Model):
    """A helper's Stripe Express account, which receives their destination charges
    (hard rule b) and pays out to the bank account they gave Stripe.

    The state is copied from `account.updated` webhooks (`payments.connect`), never from
    a client. `status` is derived from that state; only ACTIVE lets the helper send
    offers (`helpers.services.missing_requirements`). Nothing here is personal data
    beyond the link to the helper: Stripe keeps the identity documents and the bank
    account, and `requirements_due` holds Stripe's requirement keys, not values.
    """

    class Status(models.TextChoices):
        # Express onboarding not finished yet.
        INCOMPLETE = "INCOMPLETE"
        # Stripe asks for more information (currently or past due): offers are paused.
        NEEDS_INFO = "NEEDS_INFO"
        # Everything was given; Stripe is still checking it.
        VERIFYING = "VERIFYING"
        # Can receive destination charges: charges enabled, card_payments and transfers
        # active, nothing due.
        ACTIVE = "ACTIVE"
        # Stripe paused the account for a reason the helper can't fix (under review).
        RESTRICTED = "RESTRICTED"
        # Stripe rejected the account. Support explains; an appeal is possible.
        REJECTED = "REJECTED"

    helper = models.OneToOneField(
        "helpers.HelperProfile", on_delete=models.PROTECT, related_name="connected_account"
    )
    stripe_account_id = models.CharField(max_length=255, unique=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.INCOMPLETE)
    details_submitted = models.BooleanField(default=False)
    charges_enabled = models.BooleanField(default=False)
    payouts_enabled = models.BooleanField(default=False)
    card_payments_capability = models.CharField(max_length=20, blank=True)
    transfers_capability = models.CharField(max_length=20, blank=True)
    # Stripe requirement keys currently or past due, e.g. "individual.verification.document".
    requirements_due = ArrayField(models.CharField(max_length=200), default=list, blank=True)
    current_deadline = models.DateTimeField(null=True, blank=True)
    # Stripe's `requirements.disabled_reason`, e.g. "rejected.fraud" or "under_review".
    disabled_reason = models.CharField(max_length=100, blank=True)
    # `created` of the newest event applied, so an older event arriving late is ignored.
    stripe_state_at = models.DateTimeField(null=True, blank=True)
    # Counts onboarding links; each link's idempotency key carries its number.
    onboarding_links_created = models.PositiveIntegerField(default=0)
    # "More info needed" reminders sent since the account last entered NEEDS_INFO.
    info_reminders_sent = models.PositiveIntegerField(default=0)
    last_info_notice_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "stripe_connected_accounts"

    def __str__(self) -> str:
        return f"ConnectedAccount {self.pk} {self.status}"


class ConnectedAccountEvent(AuditEvent):
    """Append-only history of a connected account's status changes (hard rule c)."""

    account = models.ForeignKey(ConnectedAccount, on_delete=models.PROTECT, related_name="events")

    class Meta(AuditEvent.Meta):
        db_table = "stripe_connected_account_events"


class DemoStripeObject(models.Model):
    """An object of the demo Stripe stand-in (payments/demo_stripe.py), used only when
    DEMO_PAYMENTS is on: a PaymentIntent, charge, transfer, refund, payout, account or a
    webhook event waiting to be delivered. Never a real Stripe object, and never written in
    production (settings refuse DEMO_PAYMENTS there)."""

    id = models.CharField(primary_key=True, max_length=255)
    object = models.CharField(max_length=40)
    data = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    # Events only: when the event went through the webhook handler (payments/webhooks.py).
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "demo_stripe_objects"
        indexes: ClassVar = [
            models.Index(
                fields=["object", "created_at"],
                name="demo_stripe_undelivered_idx",
                condition=models.Q(delivered_at__isnull=True),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.id} {self.object}"


class DemoStripeRequest(models.Model):
    """A demo Stripe request answered under an idempotency key: the same key gets the same
    answer back without doing anything again, as at Stripe."""

    idempotency_key = models.CharField(primary_key=True, max_length=255)
    method = models.CharField(max_length=10)
    path = models.CharField(max_length=255)
    status = models.PositiveSmallIntegerField()
    response = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "demo_stripe_requests"

    def __str__(self) -> str:
        return f"{self.method} {self.path}"
