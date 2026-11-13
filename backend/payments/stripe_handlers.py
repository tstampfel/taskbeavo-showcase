"""Stripe webhook handlers of the payments app (registered by PaymentsConfig.ready)."""

from typing import Any

from payments import connect
from payments.webhooks import stripe_webhook_handler


@stripe_webhook_handler("account.updated")
def on_account_updated(event: dict[str, Any]) -> None:
    connect.apply_account_update(event)
