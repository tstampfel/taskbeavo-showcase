"""Stripe webhook handlers of the bookings app (registered by PaymentsConfig.ready).

Chargebacks (`charge.dispute.created`, `charge.dispute.closed`) have no handler yet:
TODO(BEAV-127). The webhook stores such events unprocessed and answers 200; the
`payments.stripe_events.dispatch_pending` beat task hands them to the handler once it
ships. A chargeback on a RELEASED or PAID_OUT booking is flagged for support by
`apply_stripe_transition`, never a 500.
"""

import logging
from typing import Any

from bookings import checkout, payouts, settlement
from payments.webhooks import stripe_webhook_handler

logger = logging.getLogger(__name__)


@stripe_webhook_handler("payment_intent.succeeded")
def on_payment_intent_succeeded(event: dict[str, Any]) -> None:
    """A checkout's payment succeeded: FUNDS_HELD, or a full refund if the checkout had
    already failed (bookings/checkout.py, `confirm_payment`)."""
    intent = event["data"]["object"]
    if checkout.confirm_payment(intent, stripe_event_id=event["id"]) is None:
        # Not a booking's PaymentIntent (another platform object): nothing to do.
        logger.warning(
            "stripe webhook: no booking for the PaymentIntent",
            extra={"stripe.event_id": event["id"], "stripe.object_id": intent.get("id")},
        )


@stripe_webhook_handler("payment_intent.payment_failed")
def on_payment_intent_payment_failed(event: dict[str, Any]) -> None:
    """A card was declined or 3D Secure failed: the checkout stays open for another card
    (bookings/checkout.py, `record_failed_attempt`)."""
    intent = event["data"]["object"]
    if checkout.record_failed_attempt(intent, stripe_event_id=event["id"]) is None:
        logger.warning(
            "stripe webhook: no booking for the PaymentIntent",
            extra={"stripe.event_id": event["id"], "stripe.object_id": intent.get("id")},
        )


@stripe_webhook_handler("payment_intent.requires_action")
def on_payment_intent_requires_action(event: dict[str, Any]) -> None:
    """A 3D Secure or bank-app confirmation started: it restarts the checkout's 30 minutes
    (bookings/checkout.py, `record_action_required`)."""
    intent = event["data"]["object"]
    if checkout.record_action_required(intent, stripe_event_id=event["id"]) is None:
        logger.warning(
            "stripe webhook: no booking for the PaymentIntent",
            extra={"stripe.event_id": event["id"], "stripe.object_id": intent.get("id")},
        )


@stripe_webhook_handler("payout.failed")
def on_payout_failed(event: dict[str, Any]) -> None:
    """The helper's bank returned a payout (bookings/payouts.py)."""
    if payouts.record_payout_failure(event) is None:
        logger.info(
            "stripe webhook: the failed payout isn't a booking's",
            extra={"stripe.event_id": event["id"]},
        )


@stripe_webhook_handler("transfer.reversed")
def on_transfer_reversed(event: dict[str, Any]) -> None:
    """A reversal of a booking's transfer: it confirms a settlement with no poster refund
    (R = 0), whose reversal is its last step (bookings/settlement.py, `confirm_reversal`).
    With R > 0 charge.refunded confirms, and this event changes nothing."""
    transfer = event["data"]["object"]
    request = event.get("request")
    key = request.get("idempotency_key") if isinstance(request, dict) else None
    if (
        settlement.confirm_reversal(transfer, stripe_event_id=event["id"], idempotency_key=key)
        is None
    ):
        logger.info(
            "stripe webhook: the reversed transfer isn't a settlement's",
            extra={"stripe.event_id": event["id"], "stripe.object_id": transfer.get("id")},
        )


@stripe_webhook_handler("charge.refunded")
def on_charge_refunded(event: dict[str, Any]) -> None:
    """A refund on a booking's charge: a cancellation's settlement is confirmed once the
    poster's refund is (bookings/settlement.py, `confirm_refund`). Late-payment refunds
    of failed checkouts need nothing here."""
    charge = event["data"]["object"]
    if settlement.confirm_refund(charge, stripe_event_id=event["id"]) is None:
        logger.info(
            "stripe webhook: the refunded charge isn't a cancellation's",
            extra={"stripe.event_id": event["id"], "stripe.object_id": charge.get("id")},
        )
