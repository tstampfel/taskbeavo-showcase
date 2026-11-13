"""Recording Stripe webhook events exactly once (hard rule a).

The webhook handler opens one transaction, calls `record_stripe_event` first, processes
the event and calls `mark_stripe_event_processed`, all inside that transaction:

    with transaction.atomic():
        if record_stripe_event(event):
            process(event)
            mark_stripe_event_processed(event["id"])
    return 200

The insert, the processing and the processed mark commit together or not at all:

- If processing fails, the insert rolls back with it, so Stripe's redelivery of the
  event inserts it again and is processed.
- A concurrent redelivery waits on the primary-key index until the first transaction
  ends. It gets `False` only if that transaction committed, and then returns 200 with
  no side effects; if the first one rolled back, its insert goes through instead.

`record_stripe_event` refuses to run in autocommit, where the row would commit on its
own before processing and a failed run would turn every redelivery into a no-op.

Retention: the raw payload is kept in full for `STRIPE_EVENT_PAYLOAD_RETENTION_DAYS`
(30 days), for debugging and replaying an event. Billing details in it (name, email,
phone, address) are personal data, so the daily `payments.stripe_events.trim` task then
replaces the payload with `trimmed_payload()`: the event's own fields, the object's ID,
type, amounts and currency, and the metadata keys TaskBeavo sets. The row is never
deleted, so its ID keeps rejecting redeliveries.
"""

import json
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from payments.models import StripeEvent

_INSERT = f"""
    INSERT INTO {StripeEvent._meta.db_table} (id, type, livemode, payload, received_at)
    VALUES (%s, %s, %s, %s::jsonb, %s)
    ON CONFLICT (id) DO NOTHING
    RETURNING id
"""  # noqa: S608 (the table name is a constant; every value is a parameter)


def record_stripe_event(event: dict[str, Any]) -> bool:
    """Insert a Stripe event with `ON CONFLICT DO NOTHING`.

    Returns True if this call stored the event, so the caller should process it, and
    False if a committed transaction already stored it, so the caller should do nothing.
    Must be called inside the `transaction.atomic()` that also processes the event.
    """
    if not connection.in_atomic_block:
        raise RuntimeError(
            "record_stripe_event() must run inside the transaction that processes the event."
        )
    with connection.cursor() as cursor:
        cursor.execute(
            _INSERT,
            [
                event["id"],
                event["type"],
                bool(event.get("livemode", False)),
                json.dumps(event),
                timezone.now(),
            ],
        )
        return cursor.fetchone() is not None


def mark_stripe_event_processed(event_id: str) -> None:
    """Stamp when the event was processed, in the same transaction as the processing.

    For audits: since the insert and the processing commit together, `processed_at`
    stays empty only for events that had no handler when they arrived. The
    `payments.stripe_events.dispatch_pending` beat task handles those once a handler
    exists (payments/webhooks.py).
    """
    StripeEvent.objects.filter(pk=event_id, processed_at__isnull=True).update(
        processed_at=timezone.now()
    )


# Fields kept after the retention period. Nothing here can hold personal data.
_EVENT_FIELDS = ("id", "object", "type", "created", "livemode", "api_version")
_OBJECT_FIELDS = ("id", "object", "currency")
# Metadata keys TaskBeavo sets on its own Stripe objects.
OWN_METADATA_KEYS = frozenset({"booking_id", "helper_profile_id", "action"})


def trimmed_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """The allow-listed, non-personal subset of a raw Stripe event payload."""
    trimmed = {key: payload[key] for key in _EVENT_FIELDS if key in payload}
    data = payload.get("data")
    obj = data.get("object") if isinstance(data, dict) else None
    if isinstance(obj, dict):
        kept = {key: obj[key] for key in _OBJECT_FIELDS if key in obj}
        kept |= {
            key: value
            for key, value in obj.items()
            if _is_amount_field(key) and isinstance(value, int) and not isinstance(value, bool)
        }
        metadata = obj.get("metadata")
        if isinstance(metadata, dict):
            own = {key: metadata[key] for key in OWN_METADATA_KEYS if key in metadata}
            if own:
                kept["metadata"] = own
        trimmed["data"] = {"object": kept}
    return trimmed


def trim_old_payloads(limit: int = 500) -> int:
    """Trim up to `limit` payloads past their retention. Returns how many were trimmed.

    Idempotent: trimmed rows are stamped with `payload_trimmed_at` and not picked
    again. Rows locked by a webhook still processing are skipped until the next run.
    """
    cutoff = timezone.now() - timedelta(days=settings.STRIPE_EVENT_PAYLOAD_RETENTION_DAYS)
    with transaction.atomic():
        rows = list(
            StripeEvent.objects.select_for_update(skip_locked=True)
            .filter(payload_trimmed_at__isnull=True, received_at__lt=cutoff)
            .order_by("received_at")[:limit]
        )
        now = timezone.now()
        for row in rows:
            row.payload = trimmed_payload(row.payload)
            row.payload_trimmed_at = now
        StripeEvent.objects.bulk_update(rows, ["payload", "payload_trimmed_at"])
    return len(rows)


def _is_amount_field(key: str) -> bool:
    return key == "amount" or key.startswith("amount_") or key.endswith("_amount")
