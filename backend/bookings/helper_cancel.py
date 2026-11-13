"""The helper's cancel and re-opening the task (hard rules a, c and g; PLAN.md, "TaskBeavo
cancellation policy" and "Other edge cases"; BEAV-123).

`cancel_as_helper` (the cancelAsHelper mutation): the helper of a booking whose money is
held (FUNDS_HELD) can't do the job. The amounts are `cancellation.cancellation_outcome`'s
HELPER_CANCEL: the poster gets everything back, fee included, and TaskBeavo funds a EUR 5
credit when it is under the late window before the start (the credit is granted once
Stripe confirms the refund, bookings/settlement.py). The helper never pays anything: the
cancel only counts against their reliability (bookings/reliability.py). It takes effect at
once, before or after the start: the booking moves to CANCEL_REQUESTED, the settlement
opens, and Stripe's webhook confirms REFUNDED. Once the helper requested payment it is
refused (BOOKING_ACTION_NOT_ALLOWED): the poster releases or opens a dispute.

`reopen_task` (the reopenTask mutation): after a helper cancel, or a no-fault cancel
(bookings/no_fault.py), the poster re-opens the task with one tap. The offers the
acceptance declined are live again and their helpers are told; the cancelled helper's
offer stays closed. The booking stops holding the task (`Booking.task_reopened_at`), so
another offer can be booked and the cancelled helper no longer sees the exact place
(hard rule e). The refund carries on regardless.
"""

import datetime as dt
from enum import StrEnum
from typing import Any

from django.db import transaction
from django.utils import timezone

from accounts.models import User
from bookings import cancellation, settlement, state_machine
from bookings.cancel import _pk, helper_is_trader
from bookings.cancellation import Case, NotACancellation, Outcome, Party
from bookings.models import Booking, BookingEvent, CancellationSettlement, HelperFault
from bookings.reliability import record_fault
from core.errors import ApiError, ErrorCode
from core.models import ActorKind
from core.outbox import enqueue
from offers.services import task_ends_at
from tasks.models import Task

S = Booking.Status

# The poster is told their helper cancelled: a live event and the booking.cancelled push.
CANCELLED_BY_HELPER_TOPIC = "booking.cancelled_by_helper"
# The helpers whose offers came back to life are told the task is open again.
TASK_REOPENED_TOPIC = "booking.task_reopened"
REOPENED = "task_reopened"
# The cancellations after which the poster may re-open the task to other offers.
REOPENABLE = frozenset({Case.HELPER_CANCEL, Case.NO_FAULT})


class HelperCancelReason(StrEnum):
    """Why the helper cancels: an enum key, never free text. No-fault reasons (weather,
    an emergency) are the no-fault flow's."""

    ILL = "ILL"
    DOUBLE_BOOKED = "DOUBLE_BOOKED"
    OTHER = "OTHER"
    # "I can't come" at the "Are you on your way?" prompt (bookings/arrival.py).
    CANT_COME = "CANT_COME"


def _now(now: dt.datetime | None) -> dt.datetime:
    return now or timezone.now()


def preview(booking: Booking, now: dt.datetime | None = None) -> Outcome:
    """What the helper's cancel of `booking` would move now: always a full refund, with
    the credit when late. Raises `NotACancellation` once payment was requested, ApiError
    when the booking isn't held."""
    if booking.status not in (S.FUNDS_HELD, S.PAYMENT_REQUESTED):
        raise ApiError(ErrorCode.BOOKING_ACTION_NOT_ALLOWED, "This booking can't be cancelled now.")
    return cancellation.cancellation_outcome(
        cancelled_by=Party.HELPER,
        no_fault_reason=None,
        amount_cents=booking.amount_cents,
        fee_cents=booking.fee_cents,
        fee_percent=booking.fee_percent,
        accepted_at=booking.created_at,
        slot_start=booking.slot_start,
        now=_now(now),
        helper_is_trader=helper_is_trader(booking),
        payment_requested=booking.status == S.PAYMENT_REQUESTED,
    )


def cancel_as_helper(
    helper: User,
    booking_id: Any,
    reason: HelperCancelReason,
    *,
    now: dt.datetime | None = None,
) -> Booking:
    """The helper cancels (module docstring). Returns the booking, CANCEL_REQUESTED."""
    now = _now(now)
    with transaction.atomic():
        try:
            booking = Booking.objects.select_for_update().get(pk=_pk(booking_id))
        except Booking.DoesNotExist:
            raise ApiError(ErrorCode.BOOKING_NOT_FOUND, "No such booking.") from None
        if booking.helper_id != helper.pk:
            raise ApiError(ErrorCode.BOOKING_NOT_FOUND, "No such booking.")
        try:
            outcome = preview(booking, now)
        except NotACancellation as refused:
            raise ApiError(refused.error_code, "This booking can't be cancelled.") from None
        booking = state_machine.transition(
            booking.pk,
            S.CANCEL_REQUESTED,
            actor_kind=ActorKind.HELPER,
            actor=helper,
            data={"reason": reason.value, "case": outcome.case.value},
            now=now,
        )
        booking.cancel_reason = reason.value
        booking.save(update_fields=["cancel_reason", "updated_at"])
        settle_helper_fault(booking, outcome, HelperFault.Kind.CANCELLED, now)
    return booking


def settle_helper_fault(
    booking: Booking,
    outcome: Outcome,
    kind: HelperFault.Kind,
    now: dt.datetime,
    *,
    decided: bool = False,
    tell_poster: bool = True,
) -> None:
    """Open the full-refund settlement of a helper-fault cancel of `booking`, locked and
    CANCEL_REQUESTED in this transaction, count it against the helper and tell the
    poster. Also used when support rejects a helper's no-fault reason: the poster then
    hears it from support's decision, so the live event goes without the push and email
    (`decided` in the payload). A no-show the poster confirmed (bookings/arrival.py) tells
    the helper instead (`tell_poster=False`)."""
    if outcome.case != Case.HELPER_CANCEL:
        raise ValueError("Only a helper cancel counts against the helper.")
    settlement.open_settlement(booking, outcome)
    record_fault(booking, kind, now)
    if not tell_poster:
        return
    enqueue(
        CANCELLED_BY_HELPER_TOPIC,
        aggregate=booking,
        payload={**_ids(booking), "decided": True} if decided else _ids(booking),
    )


def can_reopen(booking: Booking, *, case: str | None = None) -> bool:
    """Whether the poster may re-open `booking`'s task now (without locks, for the
    screens; `reopen_task` checks again under them). `case`: the settlement's case when
    the caller already read it."""
    if booking.task_reopened_at is not None:
        return False
    case = case or _settled_case(booking)
    if case not in REOPENABLE:
        return False
    task = Task.objects.filter(pk=booking.task_id).first()
    return task is not None and _task_open(task, timezone.now())


def reopen_task(poster: User, booking_id: Any, *, now: dt.datetime | None = None) -> Booking:
    """The poster re-opens the task after a helper or no-fault cancel (module docstring).
    Errors: BOOKING_NOT_FOUND, BOOKING_ACTION_NOT_ALLOWED (no such cancel, or already
    re-opened), TASK_NOT_OPEN_FOR_OFFERS (the task closed or its dates passed)."""
    now = _now(now)
    pk = _pk(booking_id)
    with transaction.atomic():
        ids = Booking.objects.filter(pk=pk).values_list("task_id", "poster_id").first()
        if ids is None:
            raise ApiError(ErrorCode.BOOKING_NOT_FOUND, "No such booking.")
        task_id, poster_id = ids
        # Poster, task, then booking: the order create_booking locks them in. Re-opening
        # checks the poster's business status (BEAV-195), which can't change meanwhile.
        state_machine.lock_poster(poster_id)
        task = Task.objects.select_for_update().get(pk=task_id)
        booking = Booking.objects.select_for_update().get(pk=pk)
        if booking.poster_id != poster.pk:
            raise ApiError(ErrorCode.BOOKING_NOT_FOUND, "No such booking.")
        case = _settled_case(booking)
        if booking.task_reopened_at is not None or case not in REOPENABLE:
            raise ApiError(
                ErrorCode.BOOKING_ACTION_NOT_ALLOWED, "This task can't be re-opened from here."
            )
        if not _task_open(task, now):
            raise ApiError(ErrorCode.TASK_NOT_OPEN_FOR_OFFERS, "The task is no longer open.")
        booking.task_reopened_at = now
        booking.save(update_fields=["task_reopened_at", "updated_at"])
        offers = state_machine.reopen_declined_offers(booking, now)
        BookingEvent.objects.create(
            booking=booking,
            type=REOPENED,
            actor_kind=ActorKind.POSTER,
            actor=poster,
            data={"offers": [str(offer.pk) for offer in offers]},
        )
        enqueue(
            TASK_REOPENED_TOPIC,
            aggregate=booking,
            payload={**_ids(booking), "offer_ids": [str(offer.pk) for offer in offers]},
        )
        enqueue(
            state_machine.UPDATED_TOPIC,
            aggregate=booking,
            payload={"booking_id": str(booking.pk), "status": booking.status},
        )
    return booking


def _settled_case(booking: Booking) -> str | None:
    settled = CancellationSettlement.objects.filter(booking=booking)
    return settled.values_list("case", flat=True).first()


def _task_open(task: Task, now: dt.datetime) -> bool:
    ends = task_ends_at(task)
    return task.status == Task.Status.OPEN and (ends is None or ends > now)


def _ids(booking: Booking) -> dict[str, str]:
    return {"booking_id": str(booking.pk), "task_id": str(booking.task_id)}
