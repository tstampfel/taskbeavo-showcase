"""The poster's cancel and the withdrawal button (hard rule g; PLAN.md, "TaskBeavo
cancellation policy (revised, consumer-safe)" and "Cancellation and edge-case flow";
BEAV-122).

`cancel_booking` (the cancelBooking mutation): a poster cancels a booking whose money is
held (FUNDS_HELD). The amounts are `cancellation.cancellation_outcome`'s, from the locked
booking and the checkout's `ConsentRecord.helper_is_trader`, never from the client:
a full refund within 1 h of accepting or more than 24 h before the start, the late
compensation under 24 h, the full refund of a trader's booking still inside its 14 days.
At or after the start it is refused with WORK_STARTED; once the helper requested payment
with BOOKING_ACTION_NOT_ALLOWED. Timing decides, not fault, so it takes effect at once
(no 48-hour reply, which is for no-fault requests, BEAV-123): the booking moves to
CANCEL_REQUESTED and the settlement is opened (bookings/settlement.py); Stripe's webhook
then confirms REFUNDED or PARTIALLY_SETTLED. Before an offer is accepted nothing exists to
cancel: the poster deletes the task.

`withdraw_from_contract` (the withdrawFromContract mutation; Directive 2023/2673, CRD Art.
11a): offered for a trader helper's booking inside its 14-day period until the helper
requests payment, before and after the start; WORK_STARTED never blocks it. The button
("Raskid ugovora", then "Potvrditi raskid ugovora", ZZP čl. 81.a) submits the withdrawal
statement with the poster's name, the booking and the email the confirmation goes to (čl.
81.a(3)), prefilled from the account and editable: a `WithdrawalStatement` keeps them and a
`withdrawal_statement` BookingEvent (IDs only) records it with its time, and the
confirmation is emailed to that address without delay (bookings/withdrawal_email.py, the
durable medium, čl. 81.a(6)). Before the start it is a full refund at once. After the start
the booking waits in support's "Cancellations & withdrawals" queue (the booking admin)
until support enters the share of the work done (`settle_withdrawal`); the split is
`cancellation.pro_rata_withdrawal_outcome`'s. The poster never has to contact support.

The cancelBooking mutation carries the refund the poster was shown; if a deadline passed
since (1 h after accepting, 24 h before the start), it is refused with
CANCELLATION_CHANGED and the screen shows the new preview.

A cancel or withdrawal can't be taken back, and the helper can't dispute it from the app,
once its settlement is open or its statement is in (`state_machine._check_actor`).

NEEDS REVIEW (lawyer, BEAV-39): the withdrawal function flow, how the 14-day period is
counted, and the wording of the withdrawal statement and its confirmation.
"""

import datetime as dt
import uuid
from enum import StrEnum
from typing import Any

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from bookings import cancellation, settlement, state_machine
from bookings.cancellation import NotACancellation, Outcome, Party
from bookings.models import (
    Booking,
    BookingEvent,
    CancellationSettlement,
    ConsentRecord,
    WithdrawalStatement,
)
from core.errors import ApiError, ErrorCode
from core.i18n import recipient_locale
from core.models import ActorKind
from core.outbox import enqueue
from staff.roles import Capability, can

S = Booking.Status


class PosterCancelReason(StrEnum):
    """Why the poster cancels: an enum key, never free text. The no-fault reasons
    (weather, hospital, emergency) are the no-fault flow's (BEAV-123)."""

    NO_LONGER_NEEDED = "NO_LONGER_NEEDED"
    DIFFERENT_DATE = "DIFFERENT_DATE"
    HELPER_ASKED = "HELPER_ASKED"
    OTHER = "OTHER"


# `Booking.cancel_reason` of a withdrawal from the contract.
WITHDRAWAL_REASON = state_machine.WITHDRAWAL_CANCEL_REASON
STATEMENT = "withdrawal_statement"
# What the statement says, as a key the email renders in the poster's language.
STATEMENT_WITHDRAW = "WITHDRAW_FROM_CONTRACT"
NAME_MAX_LENGTH = 200
SHARE_ENTERED = "withdrawal_share_entered"
# Live event and helper notification of a poster's cancel or withdrawal.
CANCELLED_TOPIC = "booking.cancelled_by_poster"
# The poster's withdrawal statement is in: their confirmation email, and support's queue.
WITHDRAWAL_RECEIVED_TOPIC = "booking.withdrawal_received"


def _now(now: dt.datetime | None) -> dt.datetime:
    return now or timezone.now()


def _pk(booking_id: Any) -> uuid.UUID:
    try:
        return uuid.UUID(str(booking_id))
    except ValueError:
        raise ApiError(ErrorCode.BOOKING_NOT_FOUND, "No such booking.") from None


def _lock_posters(poster: User, booking_id: Any) -> Booking:
    try:
        booking = Booking.objects.select_for_update().get(pk=_pk(booking_id))
    except Booking.DoesNotExist:
        raise ApiError(ErrorCode.BOOKING_NOT_FOUND, "No such booking.") from None
    if booking.poster_id != poster.pk:
        raise ApiError(ErrorCode.BOOKING_NOT_FOUND, "No such booking.")
    return booking


def helper_is_trader(booking: Booking) -> bool:
    """The helper type the poster contracted on (the checkout snapshot)."""
    consent = ConsentRecord.objects.filter(booking=booking).only("helper_is_trader").first()
    if consent is None:
        # Every paid booking has one (bookings.checkout); without it nothing is decided.
        raise ApiError(ErrorCode.BOOKING_ACTION_NOT_ALLOWED, "The booking has no checkout record.")
    return consent.helper_is_trader


def preview(booking: Booking, now: dt.datetime | None = None) -> Outcome:
    """What a poster's cancel of `booking` would move now. Raises `NotACancellation` when
    it would be refused, and ApiError when the booking isn't in a cancellable status."""
    if booking.status == S.PAYMENT_REQUESTED:
        raise NotACancellation(cancellation.NotACancellationReason.PAYMENT_REQUESTED)
    if booking.status != S.FUNDS_HELD:
        raise ApiError(ErrorCode.BOOKING_ACTION_NOT_ALLOWED, "This booking can't be cancelled now.")
    return cancellation.cancellation_outcome(
        cancelled_by=Party.POSTER,
        no_fault_reason=None,
        amount_cents=booking.amount_cents,
        fee_cents=booking.fee_cents,
        fee_percent=booking.fee_percent,
        accepted_at=booking.created_at,
        slot_start=booking.slot_start,
        now=_now(now),
        helper_is_trader=helper_is_trader(booking),
    )


def withdrawal_available(booking: Booking, now: dt.datetime | None = None) -> bool:
    """Whether the withdrawal button is offered on `booking` now."""
    if booking.status != S.FUNDS_HELD:
        return False
    consent = ConsentRecord.objects.filter(booking=booking).only("helper_is_trader").first()
    return consent is not None and cancellation.withdrawal_available(
        helper_is_trader=consent.helper_is_trader,
        accepted_at=booking.created_at,
        now=_now(now),
        payment_requested=False,
    )


def cancel_booking(
    poster: User,
    booking_id: Any,
    reason: PosterCancelReason,
    *,
    expected_refund_cents: int | None = None,
    now: dt.datetime | None = None,
) -> Booking:
    """The poster cancels (module docstring). Returns the booking, CANCEL_REQUESTED.

    `expected_refund_cents` is the refund the poster was shown (the mutation requires it):
    a deadline passing while the screen is open refuses with CANCELLATION_CHANGED rather
    than refunding less than shown. None skips the check, for server-side callers."""
    now = _now(now)
    with transaction.atomic():
        booking = _lock_posters(poster, booking_id)
        try:
            outcome = preview(booking, now)
        except NotACancellation as refused:
            raise ApiError(refused.error_code, "This booking can't be cancelled.") from None
        if expected_refund_cents is not None and outcome.refund_cents != expected_refund_cents:
            raise ApiError(ErrorCode.CANCELLATION_CHANGED, "The refund changed; check it again.")
        booking = state_machine.transition(
            booking.pk,
            S.CANCEL_REQUESTED,
            actor_kind=ActorKind.POSTER,
            actor=poster,
            data={"reason": reason.value, "case": outcome.case.value},
            now=now,
        )
        booking.cancel_reason = reason.value
        booking.save(update_fields=["cancel_reason", "updated_at"])
        settlement.open_settlement(booking, outcome)
        enqueue(CANCELLED_TOPIC, aggregate=booking, payload=_ids(booking))
    return booking


def withdraw_from_contract(
    poster: User,
    booking_id: Any,
    *,
    name: str | None = None,
    email: str | None = None,
    now: dt.datetime | None = None,
) -> Booking:
    """The poster's withdrawal statement (module docstring). `name` and `email` default to
    the account's; INVALID_WITHDRAWAL_DETAILS if either is empty or not valid."""
    now = _now(now)
    with transaction.atomic():
        booking = _lock_posters(poster, booking_id)
        name, email = _statement_details(poster, name, email)
        if booking.status not in (S.FUNDS_HELD, S.PAYMENT_REQUESTED):
            raise ApiError(
                ErrorCode.BOOKING_ACTION_NOT_ALLOWED, "This booking can't be withdrawn from now."
            )
        try:
            outcome = cancellation.withdrawal_outcome(
                amount_cents=booking.amount_cents,
                fee_cents=booking.fee_cents,
                fee_percent=booking.fee_percent,
                accepted_at=booking.created_at,
                slot_start=booking.slot_start,
                now=now,
                helper_is_trader=helper_is_trader(booking),
                payment_requested=booking.status == S.PAYMENT_REQUESTED,
            )
        except NotACancellation as refused:
            raise ApiError(refused.error_code, "There is no withdrawal to make.") from None
        booking = state_machine.transition(
            booking.pk,
            S.CANCEL_REQUESTED,
            actor_kind=ActorKind.POSTER,
            actor=poster,
            data={"reason": WITHDRAWAL_REASON, "case": outcome.case.value if outcome else ""},
            now=now,
        )
        booking.cancel_reason = WITHDRAWAL_REASON
        booking.save(update_fields=["cancel_reason", "updated_at"])
        BookingEvent.objects.create(
            booking=booking,
            type=STATEMENT,
            actor_kind=ActorKind.POSTER,
            actor=poster,
            created_at=now,
            data={
                "submitted_at": now.isoformat(),
                "statement": STATEMENT_WITHDRAW,
                "work_started": outcome is None,
                "cancellation_version": booking.cancellation_version,
            },
        )
        WithdrawalStatement.objects.create(
            booking=booking,
            name=name,
            email=email,
            language=recipient_locale(poster),
            submitted_at=now,
        )
        if outcome is not None:
            settlement.open_settlement(booking, outcome)
        enqueue(WITHDRAWAL_RECEIVED_TOPIC, aggregate=booking, payload=_ids(booking))
        enqueue(CANCELLED_TOPIC, aggregate=booking, payload=_ids(booking))
    return booking


def _statement_details(poster: User, name: str | None, email: str | None) -> tuple[str, str]:
    if name is None:
        name = f"{poster.first_name} {poster.last_name}"
    if email is None:
        email = poster.email
    name, email = " ".join(name.split()), email.strip()
    try:
        validate_email(email)
    except ValidationError:
        email = ""
    if not name or len(name) > NAME_MAX_LENGTH or not email:
        raise ApiError(
            ErrorCode.INVALID_WITHDRAWAL_DETAILS, "Give your name and a valid email address."
        )
    return name, email


def awaiting_share(booking: Booking) -> bool:
    """A withdrawal after the start that waits for support to enter the work done."""
    return (
        booking.status == S.CANCEL_REQUESTED
        and booking.cancel_reason == WITHDRAWAL_REASON
        and not CancellationSettlement.objects.filter(booking=booking).exists()
    )


# A withdrawal after the start waits for support's share; past this, support is flagged
# once (review R1-7), as the poster's money stays held until then.
SHARE_OVERDUE = "withdrawal_share_overdue"
SHARE_PATIENCE = dt.timedelta(days=2)


def flag_overdue_shares(now: dt.datetime | None = None) -> int:
    """Beat task body: flag each withdrawal whose share has waited over SHARE_PATIENCE."""
    cutoff = (now or timezone.now()) - SHARE_PATIENCE
    overdue = (
        Booking.objects.filter(
            status=S.CANCEL_REQUESTED,
            cancel_reason=WITHDRAWAL_REASON,
            settlement__isnull=True,
            events__type=STATEMENT,
            events__created_at__lte=cutoff,
        )
        .exclude(events__type=state_machine.SUPPORT_FLAG, events__data__reason=SHARE_OVERDUE)
        .distinct()
    )
    flagged = 0
    for booking in overdue:
        with transaction.atomic():
            state_machine.flag_for_support(booking, SHARE_OVERDUE, {})
        flagged += 1
    return flagged


def settle_withdrawal(staff: User, booking_id: Any, work_done_percent: int) -> Booking:
    """Support enters the share of the work done for a withdrawal after the start; the
    split comes from `cancellation.pro_rata_withdrawal_outcome` and is settled like any
    cancel. Support and finance only (the money roles, `Capability.STRIPE_IDS`)."""
    if not can(staff, Capability.STRIPE_IDS):
        raise ApiError(ErrorCode.BOOKING_ACTION_NOT_ALLOWED, "Support or finance only.")
    with transaction.atomic():
        try:
            booking = Booking.objects.select_for_update().get(pk=_pk(booking_id))
        except Booking.DoesNotExist:
            raise ApiError(ErrorCode.BOOKING_NOT_FOUND, "No such booking.") from None
        if not awaiting_share(booking):
            raise ApiError(ErrorCode.BOOKING_ACTION_NOT_ALLOWED, "No withdrawal waits for a share.")
        outcome = cancellation.pro_rata_withdrawal_outcome(
            amount_cents=booking.amount_cents,
            fee_cents=booking.fee_cents,
            fee_percent=booking.fee_percent,
            work_done_percent=work_done_percent,
        )
        # A share that leaves the poster no refund (R = 0) settles like any other: its
        # transfer reversal is the last step and transfer.reversed confirms it (user
        # decision 2026-11-07). Every share from 0 to 99% reverses something (X > 0).
        BookingEvent.objects.create(
            booking=booking,
            type=SHARE_ENTERED,
            actor_kind=ActorKind.ADMIN,
            actor=staff,
            data={"work_done_percent": work_done_percent},
        )
        settlement.open_settlement(booking, outcome)
    return booking


def _ids(booking: Booking) -> dict[str, str]:
    return {"booking_id": str(booking.pk), "task_id": str(booking.task_id)}
