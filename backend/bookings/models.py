"""Bookings: an accepted offer, from checkout to payout (docs/PLAN.md, "State machine for
task, booking and payment").

A booking is created AWAITING_PAYMENT when the poster accepts an offer and ends in one of
the terminal states (`Booking.TERMINAL`). Only `bookings.state_machine` changes
`status`; every change locks the row and writes a `BookingEvent` and an outbox row in the
same transaction (hard rule c).

Who sees what (hard rule e): `is_active` decides whether the helper sees the task's exact
address, access details and the on-site contact's phone (`tasks.schema`,
`care_contacts.policy`). It is true only while money is held for a job that can still
happen, is under way or is being wrapped up; before the payment is confirmed and after the booking
closes the helper sees the approximate area only.

Personal data: the booking holds IDs, amounts and enum keys only. The poster, helper,
task and offer are PROTECT: a booking is an accounting record (kept 11 years, hard rule
h), so account deletion (BEAV-153) archives or anonymises instead of cascading.
"""

import uuid
from typing import Any, ClassVar, NoReturn

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models

from accounts.models import Session
from care_contacts.models import CareContact
from core.crypto import EncryptedTextField
from core.models import ActorKind, AuditEvent
from offers.models import Offer
from tasks.models import Task


class Booking(models.Model):
    class Status(models.TextChoices):
        # The poster accepted an offer; the PaymentIntent is waiting for confirmation (3DS).
        AWAITING_PAYMENT = "AWAITING_PAYMENT"
        # The payment failed or checkout was abandoned; the task is open again. Terminal.
        PAYMENT_FAILED = "PAYMENT_FAILED"
        # payment_intent.succeeded: the money is in the helper's Stripe balance, payouts paused.
        FUNDS_HELD = "FUNDS_HELD"
        # A late, no-fault or helper cancellation (or a no-show) waits for the other side.
        CANCEL_REQUESTED = "CANCEL_REQUESTED"
        # The helper is on site and can't get in; the poster has 48 h to choose.
        NO_ACCESS_REPORTED = "NO_ACCESS_REPORTED"
        # A safety incident paused the booking; support settles it.
        INCIDENT_PAUSED = "INCIDENT_PAUSED"
        # The helper tapped "Request payment".
        PAYMENT_REQUESTED = "PAYMENT_REQUESTED"
        # A problem report or a chargeback; staff decide. The payout is frozen.
        DISPUTED = "DISPUTED"
        # The poster (or support) released the payment; the payout is being created.
        RELEASED = "RELEASED"
        # Stripe confirmed the payout to the helper's bank account.
        PAID_OUT = "PAID_OUT"
        # Part went to the helper, the rest was refunded with a transfer reversal.
        PARTIALLY_SETTLED = "PARTIALLY_SETTLED"
        # Stripe confirmed the full refund to the poster.
        REFUNDED = "REFUNDED"
        # Review window over, nothing left to do. Terminal.
        CLOSED = "CLOSED"

    # The helper sees the exact place and the contact's phone (hard rule e), the contact
    # is locked and can't be deleted (care_contacts.policy). User decision 2026-11-03:
    # INCIDENT_PAUSED stays active, because the helper may be on site calling 112 when an
    # incident is reported; access ends when support settles it. DISPUTED isn't: disputes
    # come after the work. AWAITING_PAYMENT isn't: the payment isn't confirmed yet.
    # CANCEL_REQUESTED is active only while a no-fault request waits for an answer (user
    # decision 2026-11-07): a recorded cancel ends access at once (`is_active`).
    ACTIVE: ClassVar = (
        Status.FUNDS_HELD,
        Status.CANCEL_REQUESTED,
        Status.NO_ACCESS_REPORTED,
        Status.INCIDENT_PAUSED,
        Status.PAYMENT_REQUESTED,
    )
    # Money is on its way or not yet settled: account deletion waits (BEAV-153).
    OPEN_MONEY: ClassVar = (
        Status.AWAITING_PAYMENT,
        Status.FUNDS_HELD,
        Status.CANCEL_REQUESTED,
        Status.NO_ACCESS_REPORTED,
        Status.INCIDENT_PAUSED,
        Status.PAYMENT_REQUESTED,
        Status.DISPUTED,
        Status.RELEASED,
        Status.PARTIALLY_SETTLED,
    )
    TERMINAL: ClassVar = (Status.PAYMENT_FAILED, Status.CLOSED)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name="bookings")
    # One booking in progress per offer; after a failed checkout the same offer may be
    # booked again (`booking_one_per_offer`).
    offer = models.ForeignKey(Offer, on_delete=models.PROTECT, related_name="bookings")
    poster = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="bookings_as_poster"
    )
    helper = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="bookings_as_helper"
    )
    # The task's on-site contact when the offer was accepted; later task edits don't move it.
    # Deleting a contact clears it on closed bookings (care_contacts.services).
    care_contact = models.ForeignKey(
        CareContact,
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="bookings",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.AWAITING_PAYMENT
    )
    # The offer's price, which the helper receives; the poster pays it plus `fee_cents`.
    amount_cents = models.PositiveIntegerField()
    # TaskBeavo's service fee actually charged: the schedule's fee (12% with a €2.50
    # minimum) less `credit_cents`. It is the Stripe application fee (hard rule b), may be
    # 0 when a credit covers it, and every refund and the fiscal receipt use it.
    fee_cents = models.PositiveIntegerField()
    # The poster's TaskBeavo credit taken off the fee at acceptance (bookings/credit.py,
    # BEAV-123): the schedule's fee is `fee_cents + credit_cents`. Restored when the
    # checkout fails or the booking is refunded in full.
    credit_cents = models.PositiveIntegerField(default=0, db_default=0)
    # The fee schedule (bookings/money.py) `fee_cents` was computed from at acceptance, and
    # its rate. The booking keeps them for good: a cancellation's kept share K is this
    # percent of the helper's compensation, whatever schedule is in force later (BEAV-191).
    # The database defaults (the first schedule) are for a rolling deploy only: an instance
    # still on the old code inserts bookings without these columns. Drop them one release
    # after this one; the app always sets both.
    fee_schedule_version = models.CharField(max_length=32, db_default="1")
    fee_percent = models.PositiveSmallIntegerField(db_default=12)
    tip_cents = models.PositiveIntegerField(default=0)
    # Whether the poster booked for a business (accounts.business) when they accepted the
    # offer, for support: a business poster books registered helpers only (BEAV-195).
    poster_was_business = models.BooleanField(default=False, db_default=False)
    # When the job starts: the task window's start, or the agreed day of a flexible task.
    slot_start = models.DateTimeField()
    payment_requested_at = models.DateTimeField(null=True, blank=True)
    released_at = models.DateTimeField(null=True, blank=True)
    released_by = models.CharField(max_length=20, choices=ActorKind.choices, blank=True)
    # An enum key (BEAV-108); never free text.
    cancel_reason = models.CharField(max_length=40, blank=True)
    cancel_requested_by = models.CharField(max_length=20, choices=ActorKind.choices, blank=True)
    cancel_requested_at = models.DateTimeField(null=True, blank=True)
    # Set by the refund flows from bookings/cancellation.py amounts (BEAV-108).
    refund_cents = models.PositiveIntegerField(null=True, blank=True)
    helper_compensation_cents = models.PositiveIntegerField(null=True, blank=True)
    # The Stripe PaymentIntent of the checkout (acceptOffer). Stored even when the checkout
    # failed meanwhile, so the payment can still be cancelled or refunded (bookings.checkout).
    payment_intent_id = models.CharField(max_length=255, blank=True)
    # A full refund of a payment that arrived after the checkout had failed (withdrawn
    # offer, abandoned checkout): the poster paid for a booking that didn't happen.
    late_payment_refund_id = models.CharField(max_length=255, blank=True)
    # The TERMS and CANCELLATION versions in force when the offer was accepted; the booking
    # stays under them after a newer version takes effect (plan: "When the terms change").
    terms_version = models.CharField(max_length=32, blank=True)
    cancellation_version = models.CharField(max_length=32, blank=True)
    # The offers this booking's acceptance declined, reopened if its checkout fails.
    declined_offer_ids = ArrayField(models.UUIDField(), default=list, blank=True)
    # The poster re-opened the task to other offers after a helper or no-fault cancel
    # (bookings/helper_cancel.py, BEAV-123). The booking then no longer holds the task:
    # another offer may be booked, and its helper no longer sees the exact place.
    task_reopened_at = models.DateTimeField(null=True, blank=True)
    status_changed_at = models.DateTimeField()
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "bookings"
        ordering: ClassVar = ["-created_at"]
        constraints: ClassVar = [
            # One booking in progress per task; a failed checkout, or a cancelled booking
            # whose task the poster re-opened, may be followed by another.
            models.UniqueConstraint(
                fields=["task"],
                condition=~models.Q(status__in=["PAYMENT_FAILED"])
                & models.Q(task_reopened_at__isnull=True),
                name="booking_one_per_task",
            ),
            models.UniqueConstraint(
                fields=["offer"],
                condition=~models.Q(status__in=["PAYMENT_FAILED"]),
                name="booking_one_per_offer",
            ),
            models.CheckConstraint(
                condition=models.Q(fee_cents__lt=models.F("amount_cents")),
                name="booking_fee_below_amount",
            ),
        ]
        indexes: ClassVar = [
            models.Index(fields=["helper", "status"], name="booking_helper_status"),
            models.Index(fields=["poster", "status"], name="booking_poster_status"),
            models.Index(fields=["care_contact", "status"], name="booking_contact_status"),
            models.Index(
                fields=["payment_intent_id"],
                name="booking_payment_intent",
                condition=~models.Q(payment_intent_id=""),
            ),
        ]

    def __str__(self) -> str:
        return f"Booking {self.pk} {self.status}"

    @property
    def is_active(self) -> bool:
        """Accepted, paid and not yet closed (care_contacts.policy.BookingLike), and still
        holding its task: a re-opened task's cancelled booking isn't. A cancel ends it the
        moment it is recorded (helper cancel, poster cancel, withdrawal, an agreed or
        upheld no-fault request); only a pending no-fault request keeps it, and a booking
        back in FUNDS_HELD is active again (user decision 2026-11-07, hard rule e)."""
        if self.status not in self.ACTIVE or self.task_reopened_at is not None:
            return False
        if self.status != self.Status.CANCEL_REQUESTED:
            return True
        return NoFaultRequest.objects.filter(booking=self, closed_at__isnull=True).exists()


class BookingEvent(AuditEvent):
    """Audit trail of a booking (hard rule c), and dispute evidence. `type` is the target
    status in lower case; `data` holds `from`, `to` and IDs, cents and enum keys only."""

    booking = models.ForeignKey(Booking, on_delete=models.PROTECT, related_name="events")

    class Meta(AuditEvent.Meta):
        db_table = "booking_events"


class ConsentRecordQuerySet(models.QuerySet["ConsentRecord"]):
    def update(self, **kwargs: Any) -> NoReturn:
        raise ValueError("Consent records are append-only.")

    def delete(self) -> NoReturn:  # pyright: ignore[reportIncompatibleMethodOverride]
        raise ValueError("Consent records are append-only.")


class ConsentRecord(models.Model):
    """The poster's checkout consent for one booking (plan: "Consumer withdrawal law turns
    TaskBeavo's fee into a refundable item"; hard rule g), the proof that they asked for
    the task to be arranged inside the 14-day withdrawal period and were told what that
    means. Written with the booking, before any PaymentIntent exists (bookings.checkout).

    It names the versions of the texts shown: the TERMS in force, and the CANCELLATION
    policy, which holds the withdrawal information (`withdrawal_notice_version`), in the
    language shown. `helper_is_trader` is the "Private individual" or "Registered business"
    label the poster saw, which decides whether the task itself carries a withdrawal right;
    `consent_variant` is which of the policy's two tick-box pairs the poster ticked
    (legal/checkout_consent.py). acceptOffer refuses a variant that doesn't match the
    label, so the two always agree. Empty only on records from before the split.

    For a trader helper it also snapshots the trader identity the contract confirmation
    shows (CRD Art. 6(1)(b)-(c), review R1-4/R1-5): the full business name, the business
    OIB (encrypted, hard rule e) and the registered business address, as they were at
    checkout, so later profile edits don't change what was confirmed. A trader must show
    these; a private helper's are never stored here, so those fields stay empty.

    Append-only legal evidence, kept 11 years like the booking (hard rule h); apart from
    the trader identity it holds versions, times and enum keys only.
    """

    booking = models.OneToOneField(Booking, on_delete=models.PROTECT, related_name="consent")
    express_request_at = models.DateTimeField()
    terms_version = models.CharField(max_length=32)
    cancellation_version = models.CharField(max_length=32)
    withdrawal_notice_version = models.CharField(max_length=32)
    language = models.CharField(max_length=2)
    helper_is_trader = models.BooleanField()
    consent_variant = models.CharField(
        max_length=10, choices=[("private", "private"), ("trader", "trader")], blank=True
    )
    trader_name = models.CharField(max_length=200, blank=True)
    trader_oib = EncryptedTextField(blank=True)
    trader_address = models.CharField(max_length=400, blank=True)
    platform = models.CharField(max_length=10, choices=Session.Platform.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    objects: ClassVar[models.Manager["ConsentRecord"]] = ConsentRecordQuerySet.as_manager()  # pyright: ignore[reportIncompatibleVariableOverride]

    class Meta:
        db_table = "consent_records"

    def __str__(self) -> str:
        return f"ConsentRecord {self.pk}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValueError("Consent records are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise ValueError("Consent records are append-only.")


class WithdrawalStatementQuerySet(models.QuerySet["WithdrawalStatement"]):
    def update(self, **kwargs: Any) -> NoReturn:
        raise ValueError("Withdrawal statements are append-only.")

    def delete(self) -> NoReturn:  # pyright: ignore[reportIncompatibleMethodOverride]
        raise ValueError("Withdrawal statements are append-only.")


class WithdrawalStatement(models.Model):
    """The poster's withdrawal statement through the withdrawal button (ZZP čl. 81.a;
    bookings/cancel.py), as submitted: the name, the email the confirmation goes to
    (čl. 81.a(3)), prefilled from the account and editable, and when it came in. The
    booking is the contract it names. The `withdrawal_statement` BookingEvent beside it
    holds IDs only (core/payloads.py), so the personal details live here.

    Append-only legal evidence of the withdrawal, kept with the booking's records (hard
    rule h); account deletion (gdpr-data-request) must keep it in the restricted archive,
    not erase it. NEEDS REVIEW (lawyer, BEAV-39): that retention.
    """

    booking = models.OneToOneField(
        Booking, on_delete=models.PROTECT, related_name="withdrawal_statement"
    )
    name = models.CharField(max_length=200)
    email = models.EmailField()
    # The poster's language when they submitted it.
    language = models.CharField(max_length=2)
    submitted_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    objects: ClassVar[models.Manager["WithdrawalStatement"]] = (  # pyright: ignore[reportIncompatibleVariableOverride]
        WithdrawalStatementQuerySet.as_manager()
    )

    class Meta:
        db_table = "withdrawal_statements"

    def __str__(self) -> str:
        return f"WithdrawalStatement {self.pk}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValueError("Withdrawal statements are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise ValueError("Withdrawal statements are append-only.")


class CancellationSettlement(models.Model):
    """How a cancelled booking's money is settled at Stripe, and how far it got (hard rule
    g; bookings/settlement.py; .reviews/BEAV-106-partial-refund-handoff.md).

    The amounts are the `bookings.cancellation.Outcome` the cancel computed, in cents, and
    are rebuilt into one (checking them again) before every Stripe step. The three steps
    run in order: fee refund Y (TaskBeavo → the helper's account), transfer reversal X
    (helper → TaskBeavo), poster refund R (TaskBeavo → the poster). Each stores its Stripe
    ID once done, when it was first sent (so a resend after a lost answer or after
    Stripe's 24-hour key window looks it up first) and its idempotency key version.
    `confirmed_at` is set when Stripe's charge.refunded confirms the poster's refund and
    the booking became REFUNDED or PARTIALLY_SETTLED; the helper's compensation C is then
    in their Stripe balance (its payout is BEAV-117).

    Independent of why the booking is settled: poster cancel and withdrawal here, the
    no-access call-out (BEAV-125) next. An accounting record (11 years, hard rule h):
    IDs, cents and enum keys only.
    """

    booking = models.OneToOneField(Booking, on_delete=models.PROTECT, related_name="settlement")
    # `bookings.cancellation.Case`.
    case = models.CharField(max_length=40)
    work_done_percent = models.PositiveSmallIntegerField(null=True, blank=True)
    amount_cents = models.PositiveIntegerField()
    fee_cents = models.PositiveIntegerField()
    helper_compensation_cents = models.PositiveIntegerField()
    kept_fee_cents = models.PositiveIntegerField()
    refund_cents = models.PositiveIntegerField()
    transfer_reversal_cents = models.PositiveIntegerField()
    fee_refund_cents = models.PositiveIntegerField()
    poster_credit_cents = models.PositiveIntegerField(default=0)
    # The destination charge's parts, read from Stripe before the first step.
    charge_id = models.CharField(max_length=255, blank=True)
    transfer_id = models.CharField(max_length=255, blank=True)
    application_fee_id = models.CharField(max_length=255, blank=True)
    fee_refund_id = models.CharField(max_length=255, blank=True)
    fee_refund_sent_at = models.DateTimeField(null=True, blank=True)
    fee_refund_key_version = models.PositiveSmallIntegerField(default=1)
    transfer_reversal_id = models.CharField(max_length=255, blank=True)
    transfer_reversal_sent_at = models.DateTimeField(null=True, blank=True)
    transfer_reversal_key_version = models.PositiveSmallIntegerField(default=1)
    refund_id = models.CharField(max_length=255, blank=True)
    refund_sent_at = models.DateTimeField(null=True, blank=True)
    refund_key_version = models.PositiveSmallIntegerField(default=1)
    # Stripe refused a step for good: support settles the rest by hand.
    refused_at = models.DateTimeField(null=True, blank=True)
    refused_step = models.CharField(max_length=40, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "cancellation_settlements"
        indexes: ClassVar = [
            models.Index(
                fields=["created_at"],
                name="settlement_open",
                condition=models.Q(confirmed_at__isnull=True, refused_at__isnull=True),
            ),
        ]

    def __str__(self) -> str:
        return f"CancellationSettlement {self.pk} {self.case}"


class NoFaultRequest(models.Model):
    """A no-fault cancellation request (hard rule g; PLAN.md, "TaskBeavo cancellation
    policy"; bookings/no_fault.py, BEAV-123): one side cites weather, a hospital stay, a
    bereavement, an emergency or a mutual agreement, and the other side has 48 hours to
    respond. The booking waits in CANCEL_REQUESTED meanwhile.

    How it ends (user decision 2026-11-07): the other side agrees, a full refund. The
    other side disagrees, support decides. Nobody replies in 48 h: when the poster asked,
    the request stands (a full refund); when the helper asked, support decides, and until
    then nothing changes, neither the helper's reliability nor the poster's credit.

    An audit record: enum keys, actor kinds and times only.
    """

    class Response(models.TextChoices):
        AGREED = "AGREED"
        DISAGREED = "DISAGREED"
        NO_REPLY = "NO_REPLY"

    class Decision(models.TextChoices):
        # The request stands: a full refund.
        UPHELD = "UPHELD"
        # The ordinary policy applies to whoever asked, as of when they asked.
        REJECTED = "REJECTED"

    booking = models.ForeignKey(Booking, on_delete=models.PROTECT, related_name="no_fault_requests")
    # `bookings.cancellation.NoFaultReason`.
    reason = models.CharField(max_length=40)
    requested_by = models.CharField(max_length=20, choices=ActorKind.choices)
    requested_at = models.DateTimeField()
    respond_by = models.DateTimeField()
    response = models.CharField(max_length=20, choices=Response.choices, blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    # A disagreement, or the helper's request left unanswered: support's queue.
    support_needed_at = models.DateTimeField(null=True, blank=True)
    decision = models.CharField(max_length=20, choices=Decision.choices, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    # Settled, rejected or withdrawn: no longer waiting for anyone.
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "no_fault_requests"
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["booking"],
                condition=models.Q(closed_at__isnull=True),
                name="no_fault_one_open_per_booking",
            ),
        ]
        indexes: ClassVar = [
            models.Index(
                fields=["respond_by"],
                name="no_fault_open_due",
                condition=models.Q(closed_at__isnull=True, response=""),
            ),
        ]

    def __str__(self) -> str:
        return f"NoFaultRequest {self.pk} {self.reason}"


class HelperFault(models.Model):
    """A cancellation that counts against the helper's reliability (PLAN.md, "Helpers
    never pay money penalties"; bookings/reliability.py, BEAV-123): they cancelled, didn't
    show up, or support rejected the no-fault reason they gave. It never costs the helper
    money. Two within 30 days open a `HelperReviewItem` for a person to look at.

    An audit record of the booking (IDs, an enum key and a time), kept like the booking.
    """

    class Kind(models.TextChoices):
        CANCELLED = "CANCELLED"
        NO_SHOW = "NO_SHOW"
        NO_FAULT_REJECTED = "NO_FAULT_REJECTED"

    helper = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="helper_faults"
    )
    booking = models.OneToOneField(Booking, on_delete=models.PROTECT, related_name="helper_fault")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "helper_faults"
        indexes: ClassVar = [
            models.Index(fields=["helper", "created_at"], name="helper_fault_recent"),
        ]

    def __str__(self) -> str:
        return f"HelperFault {self.pk} {self.kind}"


class HelperReviewItem(models.Model):
    """Support's review of a helper with repeated helper-fault cancellations (two within
    30 days, PLAN.md "Other edge cases"). A person decides what follows; nothing is
    blocked automatically and the helper never pays. `faults` are the bookings that
    triggered it. IDs, counts and times only."""

    class Reason(models.TextChoices):
        REPEATED_HELPER_CANCELS = "REPEATED_HELPER_CANCELS"

    helper = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="helper_review_items"
    )
    reason = models.CharField(max_length=40, choices=Reason.choices)
    faults = models.ManyToManyField(HelperFault, related_name="review_items")
    created_at = models.DateTimeField()
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "helper_review_items"
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["helper", "reason"],
                condition=models.Q(reviewed_at__isnull=True),
                name="helper_review_one_open",
            ),
        ]

    def __str__(self) -> str:
        return f"HelperReviewItem {self.pk} {self.reason}"


class PosterCredit(models.Model):
    """The EUR 5 goodwill credit TaskBeavo funds itself when a helper cancels under 24 h
    before the start (hard rule g; `cancellation.HELPER_CANCEL_CREDIT_CENTS`). Granted
    once Stripe confirmed the booking's full refund (bookings/settlement.py), one per
    booking. It is TaskBeavo's own money, never the helper's: it comes off TaskBeavo's
    service fee on the poster's later bookings (`PosterCreditUse`, bookings/credit.py,
    user decision 2026-11-07). No expiry for now (lawyer question, BEAV-39). An
    accounting record: IDs, cents and times."""

    poster = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="poster_credits"
    )
    source_booking = models.OneToOneField(
        Booking, on_delete=models.PROTECT, related_name="poster_credit"
    )
    amount_cents = models.PositiveIntegerField()
    granted_at = models.DateTimeField()

    class Meta:
        db_table = "poster_credits"

    def __str__(self) -> str:
        return f"PosterCredit {self.pk} {self.amount_cents}"


class PosterCreditUse(models.Model):
    """Part of a `PosterCredit` taken off one booking's service fee. `restored_cents` of
    it came back (bookings/credit.py, at `restored_at`): all of it when the checkout failed
    or the booking was cancelled or split (`cancellation.credit_back_cents`), kept for the
    audit. Only the rest counts against the credit."""

    credit = models.ForeignKey(PosterCredit, on_delete=models.PROTECT, related_name="uses")
    booking = models.ForeignKey(Booking, on_delete=models.PROTECT, related_name="credit_uses")
    amount_cents = models.PositiveIntegerField()
    used_at = models.DateTimeField()
    restored_at = models.DateTimeField(null=True, blank=True)
    restored_cents = models.PositiveIntegerField(default=0, db_default=0)

    class Meta:
        db_table = "poster_credit_uses"
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["credit", "booking"], name="credit_use_once"),
            models.CheckConstraint(
                condition=models.Q(amount_cents__gt=0), name="credit_use_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(restored_cents__lte=models.F("amount_cents")),
                name="credit_use_restored_at_most_used",
            ),
        ]

    def __str__(self) -> str:
        return f"PosterCreditUse {self.pk} {self.amount_cents}"


class BookingArrival(models.Model):
    """The day of the job (PLAN.md, "Helper no-show and late arrival"; bookings/arrival.py,
    BEAV-124): the helper's "On my way" (optional ETA), "Running late" (a new ETA) and
    "I've arrived" taps, the reminders and prompts sent, the poster's answers, and an early
    start both sides agreed to.

    The taps are evidence for the no-show timeline and for support in a dispute; on their
    own they never change who pays what (user decision 2026-11-07). They are one-off
    timestamps: never a location, and nothing is tracked during the task (hard rule k).
    Each one also writes a `BookingEvent` in the same transaction (hard rule c). Times and
    minutes only; created when the first of them happens.
    """

    booking = models.OneToOneField(
        Booking, on_delete=models.PROTECT, primary_key=True, related_name="arrival"
    )
    on_my_way_at = models.DateTimeField(null=True, blank=True)
    # The helper's latest ETA: from "On my way" or "Running late".
    eta_at = models.DateTimeField(null=True, blank=True)
    late_reported_at = models.DateTimeField(null=True, blank=True)
    # The poster's free cancel after "Running late" stays open until then: the new ETA, and
    # never less than LATE_ANSWER_MIN after the report (bookings/arrival.py). Set only by the
    # late report, so no later tap shortens it.
    late_answer_until = models.DateTimeField(null=True, blank=True)
    # The poster kept the booking after "Running late" (a free cancel ends it instead).
    late_accepted_at = models.DateTimeField(null=True, blank=True)
    arrived_at = models.DateTimeField(null=True, blank=True)
    # The poster answered "Did your helper come?" with yes.
    poster_confirmed_came_at = models.DateTimeField(null=True, blank=True)
    # The poster said "No, they didn't come" (bookings/arrival.py). The booking waits in
    # CANCEL_REQUESTED until `no_show_contest_by` (24 h): the helper may answer "I was
    # there", and support decides; without that, the refund and the NO_SHOW fault follow
    # (`no_show_settled_at`). User decision 2026-11-08.
    no_show_confirmed_at = models.DateTimeField(null=True, blank=True)
    no_show_contest_by = models.DateTimeField(null=True, blank=True)
    no_show_contested_at = models.DateTimeField(null=True, blank=True)
    no_show_settled_at = models.DateTimeField(null=True, blank=True)
    no_show_decision = models.CharField(
        max_length=10, choices=[("UPHELD", "UPHELD"), ("REJECTED", "REJECTED")], blank=True
    )
    no_show_decided_at = models.DateTimeField(null=True, blank=True)
    no_show_decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    # Failed tries of the uncontested sweep (review R1-1): retried with backoff from
    # `no_show_settle_retry_at`; support is alerted after a few and can settle it by hand.
    no_show_settle_attempts = models.PositiveSmallIntegerField(default=0)
    no_show_settle_retry_at = models.DateTimeField(null=True, blank=True)
    # What the timeline sent, so each goes out once (bookings/arrival.py, `run_timeline`).
    reminder_2h_sent_at = models.DateTimeField(null=True, blank=True)
    reminder_30m_sent_at = models.DateTimeField(null=True, blank=True)
    on_the_way_prompt_sent_at = models.DateTimeField(null=True, blank=True)
    poster_asked_at = models.DateTimeField(null=True, blank=True)
    # "Start now": the helper asked to start before the booked time; it lapses at the
    # booked start (`early_start_until`). The poster's confirmation moves
    # `Booking.slot_start` to that moment.
    early_start_requested_at = models.DateTimeField(null=True, blank=True)
    early_start_until = models.DateTimeField(null=True, blank=True)
    early_start_confirmed_at = models.DateTimeField(null=True, blank=True)
    early_start_declined_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "booking_arrivals"
        indexes: ClassVar = [
            models.Index(
                fields=["no_show_contest_by"],
                name="arrival_no_show_due",
                condition=models.Q(
                    no_show_contest_by__isnull=False,
                    no_show_contested_at__isnull=True,
                    no_show_settled_at__isnull=True,
                    no_show_decision="",
                ),
            ),
        ]

    def __str__(self) -> str:
        return f"BookingArrival {self.pk}"
