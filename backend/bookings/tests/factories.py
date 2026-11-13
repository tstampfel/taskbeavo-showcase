"""Test bookings in any status, and Stripe events to confirm money with."""

from itertools import count
from typing import Any

from django.db import transaction

from accounts.models import User
from bookings import state_machine
from bookings.models import Booking
from bookings.money import fee_schedule_at
from offers import services as offers
from offers.models import Offer
from offers.tests.factories import make_helper, open_task
from payments.events import record_stripe_event

_numbers = count(1)


def make_booking(
    status: Booking.Status = Booking.Status.AWAITING_PAYMENT,
    *,
    poster: User | None = None,
    helper: User | None = None,
    price_cents: int = 3_000,
    **task_fields: Any,
) -> Booking:
    """A booking created through the state machine, then put straight into `status`.

    Only tests set the status by hand: the move under test still goes through
    `bookings.state_machine`."""
    task = open_task(poster, **task_fields)
    helper = helper or make_helper(f"helper{next(_numbers)}")
    offer = offers.send_offer(helper, str(task.pk), price_cents)
    booking = book(task.poster, offer)
    if status != Booking.Status.AWAITING_PAYMENT:
        Booking.objects.filter(pk=booking.pk).update(status=status)
        booking.refresh_from_db()
    return booking


def checkout_total(price_cents: int) -> int:
    """The total the pay button shows for `price_cents` now: price plus today's fee."""
    return price_cents + state_machine.service_fee_cents(price_cents)


def checkout_fee_version() -> str:
    """The price list the checkout's fee notice quotes now."""
    return fee_schedule_at().version


def book(poster: User, offer: Offer, **fields: Any) -> Booking:
    """`create_booking` with the price and total the checkout showed: the current ones."""
    return state_machine.create_booking(
        poster,
        offer,
        expected_price_cents=offer.price_cents,
        expected_total_cents=checkout_total(offer.price_cents),
        expected_fee_schedule_version=checkout_fee_version(),
        **fields,
    )


def stripe_event(event_type: str = "payment_intent.succeeded") -> str:
    """Record a Stripe event the way the webhook does; call inside `transaction.atomic()`."""
    event_id = f"evt_test_{next(_numbers)}"
    assert transaction.get_connection().in_atomic_block
    assert record_stripe_event({"id": event_id, "type": event_type, "livemode": False})
    return event_id
