from celery import shared_task

from payments import connect, events, webhooks

TRIM_BATCH_SIZE = 500


@shared_task(name="payments.stripe_events.trim")
def trim_stripe_event_payloads() -> int:
    """Beat task (daily): trim raw Stripe payloads past their retention.

    A full batch means more rows may be waiting, so the task queues itself again.
    """
    trimmed = events.trim_old_payloads(TRIM_BATCH_SIZE)
    if trimmed == TRIM_BATCH_SIZE:
        trim_stripe_event_payloads.delay()
    return trimmed


DISPATCH_BATCH_SIZE = 100


@shared_task(name="payments.stripe_events.dispatch_pending")
def dispatch_pending_stripe_events() -> int:
    """Beat task (every 5 minutes): handle stored Stripe events that arrived before their
    handler was deployed (payments/webhooks.py)."""
    return webhooks.dispatch_pending(DISPATCH_BATCH_SIZE)


@shared_task(name="payments.connect.send_info_reminders")
def send_payout_info_reminders() -> int:
    """Beat task (daily): remind helpers whose Stripe account still needs information."""
    return connect.send_info_reminders()


@shared_task(
    name="payments.connect.reconcile_account",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=5,
)
def reconcile_connected_account(account_id: int) -> None:
    """Read a helper's Stripe account back after two `account.updated` events from the same
    second (payments/connect.py)."""
    connect.reconcile_account(account_id)


@shared_task(name="payments.connect.sweep_unsettled_accounts")
def sweep_unsettled_connected_accounts() -> int:
    """Beat task (daily): read back helpers' Stripe accounts whose status isn't settled."""
    return connect.sweep_unsettled_accounts()


@shared_task(name="payments.demo_stripe.deliver_events")
def deliver_demo_stripe_events() -> int:
    """Demo payments only: hand the demo Stripe stand-in's queued webhook events to the
    webhook handler (payments/demo_stripe.py)."""
    from payments import demo_stripe

    return demo_stripe.deliver_events()
