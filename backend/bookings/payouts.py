"""A payout to the helper's bank that failed (docs/PLAN.md, "Payout failures and account
problems").

"Release payment" pays the helper out from their own Stripe balance with a manual payout
(hard rule b), carrying `metadata.booking_id`. When their bank returns it (closed
account, wrong IBAN), Stripe sends `payout.failed` on the helper's connected account and
puts the money back in that balance. A payout can also show as paid first and fail
within 5 business days, so this applies to RELEASED and PAID_OUT bookings alike.

Stripe retries a failed payout only at the next scheduled payout interval, and manual
payouts have none: nothing retries it by itself. The booking keeps its status (the money
is the helper's either way), so this never calls `apply_stripe_transition`. Instead:

- a `payout_failed` BookingEvent with the payout ID and Stripe's failure code;
- a push and email asking the helper to update their bank details in Stripe
  (`booking.payout_failed`, bookings/outbox_handlers.py);
- a Sentry alert, IDs only, so support pays the money out again by hand.

TODO(BEAV-120): pay it out again automatically once the helper's account can take
payouts again, with a key per attempt (`booking:{id}:payout:{attempt}`).
"""

import re
import uuid
from typing import Any

from bookings import state_machine
from bookings.alerts import money_alert
from bookings.models import Booking, BookingEvent
from core.models import ActorKind
from core.outbox import enqueue
from core.transitions import locked_for_update
from payments.models import ConnectedAccount

S = Booking.Status

PAYOUT_FAILED = "payout_failed"
PAYOUT_FAILED_TOPIC = "booking.payout_failed"

_CODE = re.compile(r"[a-z0-9_]{1,64}")
_PAID_OUT_OR_ON_ITS_WAY = (S.RELEASED, S.PAID_OUT)


def record_payout_failure(event: dict[str, Any]) -> Booking | None:
    """Apply a verified payout.failed event. Webhook transaction only.

    Returns the booking, or None when the payout isn't one of a booking's (the helper paid
    out by hand from the Express dashboard). A payout on another account than the
    helper's, or on a booking that wasn't released, is flagged for support; either way the
    webhook answers 200.
    """
    payout = event["data"]["object"]
    booking_id = _booking_id_of(payout)
    if booking_id is None:
        return None
    try:
        with locked_for_update(Booking.objects, booking_id) as booking:
            ids = {"payout": str(payout.get("id") or ""), "stripe_event": event["id"]}
            if event.get("account") != _helper_account(booking):
                state_machine.flag_for_support(booking, "payout_on_another_account", ids)
                return booking
            if booking.status not in _PAID_OUT_OR_ON_ITS_WAY:
                state_machine.flag_for_support(booking, "payout_failed_before_release", ids)
                return booking
            seen = BookingEvent.objects.filter(
                booking=booking, type=PAYOUT_FAILED, data__payout=ids["payout"]
            )
            if seen.exists():
                # The same failure under another event ID.
                return booking
            code = payout.get("failure_code")
            data_code = code if isinstance(code, str) and _CODE.fullmatch(code) else "unknown"
            BookingEvent.objects.create(
                booking=booking,
                type=PAYOUT_FAILED,
                actor_kind=ActorKind.STRIPE_WEBHOOK,
                data={**ids, "failure_code": data_code},
            )
            enqueue(
                PAYOUT_FAILED_TOPIC,
                aggregate=booking,
                payload={"booking_id": str(booking.pk), "task_id": str(booking.task_id)},
            )
            money_alert(
                "bookings: a payout to the helper's bank failed; support pays it out again",
                booking_id=booking.pk,
                reason=PAYOUT_FAILED,
                stripe_event_type="payout.failed",
                stripe_event_id=event["id"],
                stripe_object_id=ids["payout"],
                stripe_error_code=data_code,
            )
    except Booking.DoesNotExist:
        return None
    return booking


def _booking_id_of(payout: dict[str, Any]) -> uuid.UUID | None:
    metadata = payout.get("metadata") or {}
    try:
        return uuid.UUID(str(metadata.get("booking_id")))
    except ValueError:
        return None


def _helper_account(booking: Booking) -> str | None:
    return (
        ConnectedAccount.objects.filter(helper__user_id=booking.helper_id)
        .values_list("stripe_account_id", flat=True)
        .first()
    )
