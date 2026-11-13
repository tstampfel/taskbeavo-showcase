from celery import shared_task

from bookings import arrival, cancel, checkout, no_fault, settlement


@shared_task(
    name="bookings.checkout.settle_failed",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=8,
)
def settle_failed_checkout_task(booking_id: str) -> None:
    """Cancel a failed checkout's PaymentIntent, or refund it in full if it was paid
    (bookings/checkout.py). Idempotent, so retries and duplicates are safe."""
    checkout.settle_failed_checkout(booking_id)


@shared_task(name="bookings.checkout.sweep_unsettled")
def sweep_unsettled_checkouts_task() -> int:
    """Beat task (every 15 minutes): settle failed checkouts whose own task gave up."""
    return checkout.sweep_unsettled_checkouts()


@shared_task(name="bookings.checkout.expire")
def expire_checkout_task(booking_id: str) -> None:
    """End one checkout nobody paid in time (bookings/checkout.py, `expire_checkout`). No
    retries of its own: the next Beat run picks the checkout up again."""
    checkout.expire_checkout(booking_id)


@shared_task(name="bookings.checkout.expire_abandoned")
def expire_abandoned_checkouts_task() -> int:
    """Beat task (every 5 minutes): end checkouts open longer than 30 minutes."""
    return checkout.expire_abandoned_checkouts()


@shared_task(
    name="bookings.cancellation.settle",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=8,
)
def settle_cancellation_task(booking_id: str) -> None:
    """Run a cancellation's remaining Stripe steps (bookings/settlement.py). Idempotent:
    retries and duplicates look each step up before sending it again."""
    settlement.settle(booking_id)


@shared_task(name="bookings.cancellation.flag_overdue_shares")
def flag_overdue_shares_task() -> int:
    """Beat task (hourly): flag withdrawals whose share of the work done waits too long."""
    return cancel.flag_overdue_shares()


@shared_task(name="bookings.cancellation.sweep")
def sweep_unsettled_cancellations_task() -> int:
    """Beat task (every 15 minutes): settle cancellations whose own task gave up."""
    pending = settlement.unsettled()
    for booking_id in pending:
        settle_cancellation_task.delay(str(booking_id))
    return len(pending)


@shared_task(name="bookings.cancellation.flag_unconfirmed")
def flag_unconfirmed_settlements_task() -> int:
    """Beat task (hourly): flag settlements sent to Stripe but unconfirmed after 24 h."""
    return settlement.flag_unconfirmed()


@shared_task(name="bookings.no_fault.settle_unanswered")
def settle_unanswered_no_fault_task() -> int:
    """Beat task (every 15 minutes): no-fault requests unanswered for 48 hours. The
    poster's stand (a full refund); the helper's go to support (bookings/no_fault.py)."""
    return no_fault.settle_unanswered()


@shared_task(name="bookings.arrival.run_timeline")
def run_arrival_timeline_task() -> int:
    """Beat task (every minute): the day-of-job reminders, the "Are you on your way?"
    prompt and the poster's "Did your helper come?" (bookings/arrival.py)."""
    return arrival.run_timeline()


@shared_task(name="bookings.arrival.settle_uncontested_no_shows")
def settle_uncontested_no_shows_task() -> int:
    """Beat task (every 15 minutes): no-shows the helper didn't contest within 24 hours
    become the helper cancellation (bookings/arrival.py)."""
    return arrival.settle_uncontested_no_shows()
