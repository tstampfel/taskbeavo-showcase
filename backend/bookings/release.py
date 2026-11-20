"""The poster's "Release payment", and releasing less by agreement (PLAN.md, "The adapted
flow keeps Airtasker's release model"; hard rules a, b, c and g; BEAV-117).

Full release (`release_payment`, the releasePayment mutation): the booking's poster, while
the helper has requested payment (PAYMENT_REQUESTED), releases the payment. The booking
moves to RELEASED through the state machine (one transaction, the booking locked, a
BookingEvent and a `booking.updated` outbox row), which also opens the helper's payout of
the price (`payouts.open_payout`, run by Celery after commit with the key
`booking:{id}:payout:release:v1`). No Stripe call happens in the mutation itself; only
`payout.paid` makes the booking PAID_OUT (hard rule a). A double tap waits on the row
lock and is then answered with the released booking: nothing is paid out twice. A
TaskBeavo credit the booking used is spent now (rule g): the fee stays reduced.

Releasing less (owner decision 2026-11-20): the poster proposes a lower amount
(`propose_partial_release`), which moves nothing. The booking stays PAYMENT_REQUESTED and
the helper is told. The poster may withdraw it (`withdraw_partial_release`) or still
release in full; the helper accepts or declines (`respond_to_partial_release`). Only the
helper's acceptance moves money: it opens a `CancellationSettlement` with the amounts of
`cancellation.partial_release_outcome` (the helper keeps the agreed part C, TaskBeavo the
booking's fee rate of it, K, and the poster gets the rest back), which bookings/settlement.py
runs at Stripe (fee refund, transfer reversal, poster refund). Stripe's webhook then makes
the booking PARTIALLY_SETTLED, gives back any credit the booking used (as for every split,
rule g: TaskBeavo took K in cash) and opens the payout of C. While that settlement runs,
neither side can move the booking (state_machine `_check_actor`). With no answer from the
helper nothing moves, ever: there is no deadline and no automatic outcome; the poster
releases in full, withdraws the proposal, or (once disputes exist) disputes.

The day of the job (bookings/arrival.py, BEAV-116): a payment request keeps the poster's
no-show "No" and free cancel after "Running late" open until the poster says "Yes, they
came". Releasing, in full or by a proposal, is refused while the poster still has a late
arrival to answer (`arrival.can_answer_late`): they keep the booking or cancel first.
Otherwise releasing says the helper came: it records "Yes, they came" in the same
transaction (if not said yet), which closes the no-show route.
"""

import datetime as dt
from typing import Any

from django.db import transaction
from django.utils import timezone

from accounts.models import User
from bookings import arrival, cancellation, settlement, state_machine
from bookings.cancel import _pk
from bookings.cancellation import Outcome
from bookings.models import (
    Booking,
    BookingArrival,
    BookingEvent,
    CancellationSettlement,
    ReleaseProposal,
)
from core.errors import ApiError, ErrorCode
from core.models import ActorKind
from core.outbox import enqueue

S = Booking.Status
P = ReleaseProposal.Status

# The poster released in full: the helper is told (bookings/outbox_handlers.py).
RELEASED_TOPIC = "booking.payment_released"
# A proposal to release less, and the helper's answer or the poster's withdrawal.
PROPOSAL_TOPIC = "booking.release_proposal"
PROPOSED = "release_proposed"
ACCEPTED = "release_proposal_accepted"
DECLINED = "release_proposal_declined"
WITHDRAWN = "release_proposal_withdrawn"


def release_payment(poster: User, booking_id: Any, *, now: dt.datetime | None = None) -> Booking:
    """The poster releases the payment in full (module docstring). Returns the booking,
    RELEASED. A repeated tap on a booking this poster already released returns it as is."""
    now = now or timezone.now()
    with transaction.atomic():
        booking, arrival_row = _lock(poster, booking_id)
        if booking.status in (S.RELEASED, S.PAID_OUT) and booking.released_by == ActorKind.POSTER:
            return booking
        _check_releasable(booking, arrival_row, now)
        _close_open_proposal(booking, P.WITHDRAWN, now)
        _confirm_came(booking, arrival_row, poster, now)
        booking = state_machine.transition(
            booking.pk, S.RELEASED, actor_kind=ActorKind.POSTER, actor=poster, now=now
        )
        enqueue(RELEASED_TOPIC, aggregate=booking, payload=_ids(booking))
    return booking


def can_release(booking: Booking, viewer: User, now: dt.datetime | None = None) -> bool:
    """Whether `viewer` gets "Release payment" (and "Release less") now."""
    if booking.poster_id != viewer.pk:
        return False
    arrival_row = BookingArrival.objects.filter(booking=booking).first()
    try:
        _check_releasable(booking, arrival_row, now or timezone.now())
    except ApiError:
        return False
    return True


def partial_release_preview(booking: Booking, released_cents: int) -> Outcome:
    """What releasing `released_cents` would move (the poster's screen, the helper's
    answer). ValueError for an amount outside 1 cent to 1 cent under the price."""
    return cancellation.partial_release_outcome(
        amount_cents=booking.amount_cents,
        fee_cents=booking.fee_cents,
        fee_percent=booking.fee_percent,
        released_cents=released_cents,
    )


def propose_partial_release(
    poster: User, booking_id: Any, released_cents: int, *, now: dt.datetime | None = None
) -> Booking:
    """The poster proposes releasing `released_cents` instead of the price. Moves no money;
    the helper is told. INVALID_RELEASE_AMOUNT outside 1 cent to 1 cent under the price,
    BOOKING_ACTION_NOT_ALLOWED when releasing isn't possible or a proposal is open."""
    now = now or timezone.now()
    with transaction.atomic():
        booking, arrival_row = _lock(poster, booking_id)
        _check_releasable(booking, arrival_row, now)
        try:
            partial_release_preview(booking, released_cents)
        except ValueError:
            raise ApiError(
                ErrorCode.INVALID_RELEASE_AMOUNT,
                "Release more than nothing and less than the price.",
            ) from None
        if ReleaseProposal.objects.filter(booking=booking, status=P.OPEN).exists():
            raise ApiError(
                ErrorCode.BOOKING_ACTION_NOT_ALLOWED, "Withdraw the open proposal first."
            )
        _confirm_came(booking, arrival_row, poster, now)
        proposal = ReleaseProposal.objects.create(
            booking=booking, released_cents=released_cents, created_at=now
        )
        _event(booking, PROPOSED, proposal, ActorKind.POSTER, poster, now)
    return booking


def withdraw_partial_release(
    poster: User, booking_id: Any, *, now: dt.datetime | None = None
) -> Booking:
    """The poster withdraws the open proposal before the helper answered."""
    now = now or timezone.now()
    with transaction.atomic():
        booking, _ = _lock(poster, booking_id)
        proposal = _open_proposal(booking)
        proposal.status = P.WITHDRAWN
        proposal.answered_at = now
        proposal.save(update_fields=["status", "answered_at"])
        _event(booking, WITHDRAWN, proposal, ActorKind.POSTER, poster, now)
    return booking


def respond_to_partial_release(
    helper: User, booking_id: Any, *, accept: bool, now: dt.datetime | None = None
) -> Booking:
    """The helper accepts or declines the open proposal. Accepting opens the settlement of
    `cancellation.partial_release_outcome` (module docstring); declining moves nothing and
    the poster is told."""
    now = now or timezone.now()
    with transaction.atomic():
        try:
            booking = Booking.objects.select_for_update().get(pk=_pk(booking_id))
        except Booking.DoesNotExist:
            raise ApiError(ErrorCode.BOOKING_NOT_FOUND, "No such booking.") from None
        if booking.helper_id != helper.pk:
            raise ApiError(ErrorCode.BOOKING_NOT_FOUND, "No such booking.")
        if not helper.is_active:
            raise ApiError(ErrorCode.UNAUTHENTICATED, "Sign in first.")
        proposal = _open_proposal(booking)
        if booking.status != S.PAYMENT_REQUESTED or _settling(booking):
            raise ApiError(ErrorCode.BOOKING_ACTION_NOT_ALLOWED, "There is nothing to answer.")
        proposal.status = P.ACCEPTED if accept else P.DECLINED
        proposal.answered_at = now
        proposal.save(update_fields=["status", "answered_at"])
        _event(booking, ACCEPTED if accept else DECLINED, proposal, ActorKind.HELPER, helper, now)
        if accept:
            settlement.open_settlement(
                booking, partial_release_preview(booking, proposal.released_cents)
            )
    return booking


def open_proposal(booking: Booking) -> ReleaseProposal | None:
    """The proposal waiting for the helper, or None."""
    return ReleaseProposal.objects.filter(booking=booking, status=P.OPEN).first()


def latest_proposal(booking: Booking) -> ReleaseProposal | None:
    return ReleaseProposal.objects.filter(booking=booking).order_by("-created_at", "-pk").first()


def _lock(poster: User, booking_id: Any) -> tuple[Booking, BookingArrival | None]:
    try:
        booking = Booking.objects.select_for_update().get(pk=_pk(booking_id))
    except Booking.DoesNotExist:
        raise ApiError(ErrorCode.BOOKING_NOT_FOUND, "No such booking.") from None
    if booking.poster_id != poster.pk:
        raise ApiError(ErrorCode.BOOKING_NOT_FOUND, "No such booking.")
    if not poster.is_active:
        raise ApiError(ErrorCode.UNAUTHENTICATED, "Sign in first.")
    return booking, BookingArrival.objects.filter(booking=booking).first()


def _check_releasable(
    booking: Booking, arrival_row: BookingArrival | None, now: dt.datetime
) -> None:
    if (
        booking.status != S.PAYMENT_REQUESTED
        or booking.task_reopened_at is not None
        or _settling(booking)
    ):
        raise ApiError(ErrorCode.BOOKING_ACTION_NOT_ALLOWED, "The helper hasn't requested payment.")
    if arrival.can_answer_late(booking, arrival_row, now):
        raise ApiError(
            ErrorCode.BOOKING_ACTION_NOT_ALLOWED, "Answer your helper's late arrival first."
        )


def _settling(booking: Booking) -> bool:
    """An accepted proposal's settlement is running at Stripe."""
    return CancellationSettlement.objects.filter(booking=booking).exists()


def _open_proposal(booking: Booking) -> ReleaseProposal:
    proposal = (
        ReleaseProposal.objects.select_for_update().filter(booking=booking, status=P.OPEN).first()
    )
    if proposal is None:
        raise ApiError(ErrorCode.BOOKING_ACTION_NOT_ALLOWED, "There is no open proposal.")
    return proposal


def _close_open_proposal(booking: Booking, status: str, now: dt.datetime) -> None:
    ReleaseProposal.objects.filter(booking=booking, status=P.OPEN).update(
        status=status, answered_at=now
    )


def _confirm_came(
    booking: Booking, arrival_row: BookingArrival | None, poster: User, now: dt.datetime
) -> None:
    """Releasing says the helper came: "Yes, they came", if not said yet (BEAV-116)."""
    if arrival_row is not None and arrival_row.poster_confirmed_came_at is not None:
        return
    arrival.record_came_on_release(booking, poster, now)


def _event(
    booking: Booking,
    type: str,
    proposal: ReleaseProposal,
    kind: ActorKind,
    actor: User,
    now: dt.datetime,
) -> None:
    BookingEvent.objects.create(
        booking=booking,
        type=type,
        actor_kind=kind,
        actor=actor,
        created_at=now,
        data={"proposal": proposal.pk, "released_cents": proposal.released_cents},
    )
    enqueue(
        PROPOSAL_TOPIC,
        aggregate=booking,
        payload={**_ids(booking), "proposal_id": str(proposal.pk), "step": type},
    )


def _ids(booking: Booking) -> dict[str, str]:
    return {"booking_id": str(booking.pk), "task_id": str(booking.task_id)}
