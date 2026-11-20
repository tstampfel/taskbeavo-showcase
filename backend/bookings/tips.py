"""The poster's tip (PLAN.md, "taps Release payment and can add a tip"; hard rules a and b;
BEAV-117).

`add_tip` (the addTip mutation): after a full release (RELEASED or PAID_OUT), the booking's
poster tips the helper once, at least `MIN_TIP_CENTS` and at most the task price. It is a
separate destination charge on behalf of the helper with no application fee: TaskBeavo
takes nothing from it, and as on every destination charge pays Stripe's card fee
(an open launch question: a tip brings no fee to cover it). The `Tip` row
is written in a transaction under the booking's lock, then the PaymentIntent is created
outside it with the key `booking:{id}:tip:v1` (metadata `action=tip`), and the client
secret goes back to the poster's payment sheet. Calling again with the same amount
returns the same PaymentIntent (the key replays it); another amount is refused while the
first is pending, since Stripe refuses a reused key with new parameters.

Nothing counts until Stripe says so (hard rule a): `confirm_tip`, on
payment_intent.succeeded for a PaymentIntent whose metadata says it is a tip, marks the tip
SUCCEEDED, sets `Booking.tip_cents`, writes a BookingEvent and a `booking.tip_received`
outbox row (the helper's push and email) and opens the tip's payout
(bookings/payouts.py). A declined card leaves the tip PENDING for another card.

No fiscal receipt: TaskBeavo charges no fee on a tip (rule f is launch-only anyway).
"""

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.utils import timezone

from accounts.models import User
from bookings import payouts, state_machine
from bookings.cancel import _pk
from bookings.models import Booking, BookingEvent, BookingPayout, Tip
from core.errors import ApiError, ErrorCode
from core.models import ActorKind
from core.outbox import enqueue
from core.transitions import locked_for_update
from payments.idempotency import idempotency_key
from payments.models import ConnectedAccount
from payments.stripe_client import TIP_ACTION, StripeRequestError, get_gateway

logger = logging.getLogger(__name__)

S = Booking.Status

MIN_TIP_CENTS = 100
TIPPABLE = (S.RELEASED, S.PAID_OUT)
RECEIVED_TOPIC = "booking.tip_received"
TIP_STARTED = "tip_started"
TIP_RECEIVED = "tip_received"
TIP_MISMATCH = "tip_mismatch"


@dataclass(frozen=True)
class TipSession:
    booking: Booking
    tip: Tip
    client_secret: str


def is_tip_intent(intent: dict[str, Any]) -> bool:
    """A PaymentIntent of a tip (its metadata), not a checkout's."""
    metadata = intent.get("metadata") or {}
    return isinstance(metadata, dict) and metadata.get("action") == TIP_ACTION


def tip_key(booking_id: Any) -> str:
    return idempotency_key("booking", str(booking_id), "tip")


def add_tip(
    poster: User, booking_id: Any, amount_cents: int, *, now: dt.datetime | None = None
) -> TipSession:
    """Start the poster's tip (module docstring). Errors: BOOKING_NOT_FOUND,
    BOOKING_ACTION_NOT_ALLOWED (not released, or already tipped), INVALID_TIP_AMOUNT,
    HELPER_CANNOT_TAKE_PAYMENTS, PAYMENT_UNAVAILABLE."""
    now = now or timezone.now()
    with transaction.atomic():
        try:
            booking = Booking.objects.select_for_update().get(pk=_pk(booking_id))
        except Booking.DoesNotExist:
            raise ApiError(ErrorCode.BOOKING_NOT_FOUND, "No such booking.") from None
        if booking.poster_id != poster.pk:
            raise ApiError(ErrorCode.BOOKING_NOT_FOUND, "No such booking.")
        if not poster.is_active:
            raise ApiError(ErrorCode.UNAUTHENTICATED, "Sign in first.")
        if booking.status not in TIPPABLE:
            raise ApiError(ErrorCode.BOOKING_ACTION_NOT_ALLOWED, "Release the payment first.")
        if (
            not isinstance(amount_cents, int)
            or isinstance(amount_cents, bool)
            or not MIN_TIP_CENTS <= amount_cents <= booking.amount_cents
        ):
            raise ApiError(ErrorCode.INVALID_TIP_AMOUNT, "That tip amount isn't possible.")
        tip = Tip.objects.select_for_update().filter(booking=booking).first()
        if tip is not None and tip.status == Tip.Status.SUCCEEDED:
            raise ApiError(ErrorCode.BOOKING_ACTION_NOT_ALLOWED, "You already tipped.")
        if tip is not None and tip.amount_cents != amount_cents:
            raise ApiError(ErrorCode.INVALID_TIP_AMOUNT, "Finish or keep the tip you started.")
        if tip is None:
            tip = Tip.objects.create(booking=booking, amount_cents=amount_cents, created_at=now)
            BookingEvent.objects.create(
                booking=booking,
                type=TIP_STARTED,
                actor_kind=ActorKind.POSTER,
                actor=poster,
                created_at=now,
                data={"amount_cents": amount_cents},
            )
        account = _destination(booking)
    try:
        intent = get_gateway().create_tip_charge(
            booking_id=str(booking.pk),
            amount_cents=amount_cents,
            destination_account=account,
            idempotency_key=tip_key(booking.pk),
        )
    except StripeRequestError as exc:
        logger.warning(
            "bookings: the tip's PaymentIntent couldn't be created",
            extra={"booking.id": str(booking.pk), "stripe.error_code": exc.code},
        )
        code = (
            ErrorCode.HELPER_CANNOT_TAKE_PAYMENTS
            if exc.is_connected_account_problem
            else ErrorCode.PAYMENT_UNAVAILABLE
        )
        raise ApiError(code, "The tip can't be paid right now.") from None
    if not tip.payment_intent_id:
        Tip.objects.filter(pk=tip.pk, payment_intent_id="").update(payment_intent_id=intent.id)
        tip.refresh_from_db()
    return TipSession(booking=booking, tip=tip, client_secret=str(intent.client_secret or ""))


def _destination(booking: Booking) -> str:
    account = (
        ConnectedAccount.objects.filter(helper__user_id=booking.helper_id)
        .values_list("stripe_account_id", "status")
        .first()
    )
    if account is None or account[1] != ConnectedAccount.Status.ACTIVE:
        raise ApiError(
            ErrorCode.HELPER_CANNOT_TAKE_PAYMENTS, "The helper's account can't take payments."
        )
    return account[0]


def confirm_tip(intent: dict[str, Any], *, stripe_event_id: str) -> Booking | None:
    """Apply a verified payment_intent.succeeded event of a tip. Webhook transaction only.
    Returns the booking, or None when no booking of ours has this tip. A PaymentIntent
    that doesn't match the tip (another ID, amount, currency or account, or a fee) is
    flagged for support and changes nothing."""
    metadata = intent.get("metadata") or {}
    try:
        pk = _pk(metadata.get("booking_id"))
    except ApiError:
        return None
    try:
        with locked_for_update(Booking.objects, pk) as booking:
            tip = Tip.objects.select_for_update().filter(booking=booking).first()
            if tip is None:
                return None
            if tip.status == Tip.Status.SUCCEEDED:
                return booking
            mismatch = _mismatch(booking, tip, intent)
            if mismatch:
                state_machine.flag_for_support(
                    booking,
                    TIP_MISMATCH,
                    {
                        "mismatch": mismatch,
                        "payment_intent": str(intent.get("id") or ""),
                        "stripe_event": stripe_event_id,
                    },
                )
                return booking
            now = timezone.now()
            tip.status = Tip.Status.SUCCEEDED
            tip.succeeded_at = now
            tip.payment_intent_id = intent["id"]
            tip.save(update_fields=["status", "succeeded_at", "payment_intent_id"])
            booking.tip_cents = tip.amount_cents
            booking.save(update_fields=["tip_cents", "updated_at"])
            BookingEvent.objects.create(
                booking=booking,
                type=TIP_RECEIVED,
                actor_kind=ActorKind.STRIPE_WEBHOOK,
                data={
                    "amount_cents": tip.amount_cents,
                    "payment_intent": intent["id"],
                    "stripe_event": stripe_event_id,
                },
            )
            enqueue(
                RECEIVED_TOPIC,
                aggregate=booking,
                payload={"booking_id": str(booking.pk), "task_id": str(booking.task_id)},
            )
            payouts.open_payout(booking, BookingPayout.Kind.TIP, tip.amount_cents)
    except Booking.DoesNotExist:
        return None
    return booking


def _mismatch(booking: Booking, tip: Tip, intent: dict[str, Any]) -> str | None:
    if tip.payment_intent_id and intent.get("id") != tip.payment_intent_id:
        return "payment_intent"
    if intent.get("amount") != tip.amount_cents:
        return "amount"
    if intent.get("currency") != "eur":
        return "currency"
    if intent.get("application_fee_amount"):
        return "application_fee"
    destination = (intent.get("transfer_data") or {}).get("destination")
    expected = (
        ConnectedAccount.objects.filter(helper__user_id=booking.helper_id)
        .values_list("stripe_account_id", flat=True)
        .first()
    )
    if destination != expected or intent.get("on_behalf_of") != expected:
        return "account"
    return None
