"""Money problems a person must act on reach Sentry the same way (user decision
2026-11-04): every path that answers Stripe 200 and leaves the case to support calls
`money_alert`. That covers a failed payout, a payment after the checkout ended, a cancel
or refund Stripe refused, Stripe failing again and again, a failed checkout past the
sweep's lookback and a Stripe move that doesn't fit the booking (a chargeback after the
payout).

Each one is a level=error event tagged `support_flag=money`, which the Sentry alert rule
(set up by the manager) notifies support on, with `booking.id`, a `support.reason` enum
key and Stripe's event type and IDs. Nothing else: no names, amounts or Stripe messages.
"""

import logging

import sentry_sdk
from django.db import transaction

TAG = "support_flag"
MONEY = "money"

logger = logging.getLogger("bookings.support")


def money_alert(
    message: str,
    *,
    booking_id: object,
    reason: str,
    stripe_event_type: str = "",
    stripe_event_id: str = "",
    stripe_object_id: str = "",
    stripe_error_code: str = "",
) -> None:
    """Send one support alert. `message` is a constant sentence, never formatted with data."""
    tags = {
        TAG: MONEY,
        "booking.id": str(booking_id),
        "support.reason": reason,
        "stripe.event_type": stripe_event_type,
        "stripe.event_id": stripe_event_id,
        "stripe.object_id": stripe_object_id,
        "stripe.error_code": stripe_error_code,
    }
    tags = {key: value for key, value in tags.items() if value}
    # After commit (review R1-3): the support flag or event the alert is about is written
    # in the same transaction, so a rollback means there is nothing to alert about. Outside
    # a transaction it is sent at once.
    transaction.on_commit(lambda: _send(message, tags), robust=True)


def _send(message: str, tags: dict[str, str]) -> None:
    with sentry_sdk.new_scope() as scope:
        for key, value in tags.items():
            scope.set_tag(key, value)
        logger.error(message, extra=tags)
