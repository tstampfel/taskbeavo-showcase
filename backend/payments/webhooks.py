"""POST /webhooks/stripe: verify, record once, dispatch (hard rule a).

1. The `Stripe-Signature` header must sign the raw body with one of
   `STRIPE_WEBHOOK_SECRETS`, within Stripe's five-minute window; otherwise 400 and
   nothing is stored.
2. An event whose `livemode` doesn't match the deployment (`STRIPE_LIVEMODE`: live in
   production, test everywhere else) is acknowledged with 200 and dropped. Stripe sends
   connected accounts' test-mode events to live Connect endpoints too, signed with the
   same secret, and one must never move a real booking.
3. In one transaction the event is inserted into `stripe_events` with
   `ON CONFLICT DO NOTHING` (`payments.events.record_stripe_event`). A replay inserts
   nothing and returns 200 without running any handler.
4. The handler registered for the event type runs in that same transaction, and the
   event is marked processed. If the handler raises, everything rolls back and the
   response is 500, so Stripe redelivers the event and it is processed then.
5. An event with no handler is stored, acknowledged and left unprocessed. That happens
   when an instance from before a deploy receives a type the new release handles, or
   when a type is subscribed before its handler ships. The beat task
   `payments.stripe_events.dispatch_pending` (`dispatch_pending` below) hands such rows
   to their handler once one is registered, until their payload is trimmed (30 days).

Registering a handler is one decorator, in a `stripe_handlers.py` module of any app
(loaded by `PaymentsConfig.ready`):

    @stripe_webhook_handler("account.updated")
    def on_account_updated(event: dict[str, Any]) -> None: ...

A handler receives the verified event as a plain dict. It applies state changes through
the state machine (rule c), writes outbox rows for anything slow or external, and
returns quickly. It must tolerate events arriving out of order: if the object is already
past the target state, do nothing. Connected-account events (`account.updated`,
`payout.*`) carry the account ID in `event["account"]`.

The request span carries `stripe.event_type` and IDs only: the payload can hold billing
details (names, email, phone, address) and is never logged or traced.
"""

import json
import logging
from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from opentelemetry import trace
from opentelemetry.trace import StatusCode

from payments.events import mark_stripe_event_processed, record_stripe_event
from payments.models import StripeEvent
from payments.stripe_client import InvalidWebhookSignatureError, verify_webhook_signature

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

Handler = Callable[[dict[str, Any]], None]

_handlers: dict[str, Handler] = {}


def stripe_webhook_handler(*event_types: str) -> Callable[[Handler], Handler]:
    """Register the decorated function for one or more Stripe event types.

    Each type has at most one handler, so a second registration raises at import time.
    """
    if not event_types:
        raise ValueError("Name at least one Stripe event type.")

    def register(handler: Handler) -> Handler:
        for event_type in event_types:
            if event_type in _handlers:
                raise ValueError(f"A handler for {event_type} is already registered.")
            _handlers[event_type] = handler
        return handler

    return register


def process_event(event: dict[str, Any]) -> bool:
    """Record and handle a verified event in one transaction. False for a replay."""
    with transaction.atomic():
        if not record_stripe_event(event):
            return False
        handler = _handlers.get(event["type"])
        if handler is not None:
            handler(event)
            mark_stripe_event_processed(event["id"])
    return True


def dispatch_pending(limit: int = 100) -> int:
    """Handle stored events that arrived before their handler existed.

    Picks unprocessed, untrimmed rows of this deployment's mode (the view never stores
    the other mode's events; this holds even if one got in) whose type now has a
    handler, oldest first, and
    handles each in its own transaction under a row lock, so a webhook processing the
    same row, or another sweep, skips it. A handler that fails is logged and its row
    retried on the next run; the other rows go ahead. Returns how many were handled.
    """
    if not _handlers:
        return 0
    pending = list(
        StripeEvent.objects.filter(
            processed_at__isnull=True,
            payload_trimmed_at__isnull=True,
            type__in=list(_handlers),
            livemode=settings.STRIPE_LIVEMODE,
        )
        .order_by("received_at")
        .values_list("pk", flat=True)[:limit]
    )
    handled = 0
    for event_id in pending:
        try:
            with transaction.atomic():
                row = (
                    StripeEvent.objects.select_for_update(skip_locked=True)
                    .filter(pk=event_id, processed_at__isnull=True)
                    .first()
                )
                if row is None:
                    continue
                _handlers[row.type](row.payload)
                mark_stripe_event_processed(row.pk)
        except Exception:
            logger.exception(
                "stripe webhook: pending event handler failed",
                extra={"stripe.event_id": event_id},
            )
            continue
        handled += 1
    return handled


@csrf_exempt
@require_POST
def stripe_webhook(request: HttpRequest) -> HttpResponse:
    payload = request.body
    try:
        verify_webhook_signature(
            payload, request.headers.get("Stripe-Signature"), settings.STRIPE_WEBHOOK_SECRETS
        )
    except InvalidWebhookSignatureError:
        logger.warning("stripe webhook: signature verification failed")
        return HttpResponse(status=400)

    event = _parse_event(payload)
    if event is None:
        logger.warning("stripe webhook: signed body is not a Stripe event")
        return HttpResponse(status=400)

    if event.get("livemode") is not settings.STRIPE_LIVEMODE:
        logger.info(
            "stripe webhook: ignored an event from the other mode",
            extra={"stripe.event_id": event["id"], "stripe.event_type": event["type"]},
        )
        return HttpResponse(status=200)

    with tracer.start_as_current_span("stripe.webhook") as span:
        span.set_attribute("stripe.event_type", event["type"])
        span.set_attribute("stripe.event_id", event["id"])
        object_id = _object_id(event)
        if object_id is not None:
            span.set_attribute("stripe.object_id", object_id)
        try:
            process_event(event)
        except Exception as exc:
            # Rolled back: Stripe redelivers. The message is filtered before Sentry
            # (config/scrubbing.py) and dropped from the span (config/telemetry.py); the
            # extra fields are IDs only.
            span.record_exception(exc)
            span.set_status(StatusCode.ERROR, type(exc).__name__)
            logger.exception(
                "stripe webhook: handler failed",
                extra={"stripe.event_id": event["id"], "stripe.event_type": event["type"]},
            )
            return HttpResponse(status=500)
    return HttpResponse(status=200)


def _parse_event(payload: bytes) -> dict[str, Any] | None:
    try:
        event = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(event, dict)
        or event.get("object") != "event"
        or not isinstance(event.get("id"), str)
        or not isinstance(event.get("type"), str)
    ):
        return None
    return event


def _object_id(event: dict[str, Any]) -> str | None:
    data = event.get("data")
    obj = data.get("object") if isinstance(data, dict) else None
    object_id = obj.get("id") if isinstance(obj, dict) else None
    return object_id if isinstance(object_id, str) else None
