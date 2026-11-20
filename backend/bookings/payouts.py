"""Payouts to the helper's bank (hard rule b; BEAV-117), and the ones that fail (docs/PLAN.md,
"Payout failures and account problems").

Paying out. The helper's Express account has manual payouts, so money leaves their Stripe
balance only when TaskBeavo creates a payout. `open_payout` records one, a `BookingPayout`,
in the transaction that made it due, with a `booking.payout_opened` outbox row whose
handler queues `pay_out` (Celery) after commit:

- RELEASE, once per booking: the price when the poster releases in full (bookings/release.py,
  RELEASED), or the helper's compensation C once Stripe confirmed a partial release or a
  cancellation's settlement (bookings/settlement.py);
- TIP: a tip, once Stripe confirmed its charge (bookings/tips.py).

`pay_out` sends `stripe.Payout.create` on the helper's account with the key
`booking:{id}:payout:{kind}:v{n}` and `metadata.action` `payout_release`/`payout_tip`. A
payout sent before without an answer saved is looked up first (`find_payout`), so a bumped
key version (after a 5xx or `balance_insufficient`, which Stripe stores under the key and
replays for 24 hours) never pays twice. Network
errors, 5xx, rate limits and `balance_insufficient` (the charge's money isn't available in
the helper's balance yet) are retried by Celery and by the sweep (`unsent`); a refusal no
retry can fix, or a payout still unsent after `SEND_PATIENCE`, goes to support
(`refused_at`, a support flag and a money alert).

`payout.paid` (`record_payout_paid`, connected-account webhook) sets `paid_at`, and moves a
RELEASED booking to PAID_OUT (hard rule a: only the webhook confirms it). A partially
settled booking keeps its status.

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

import datetime as dt
import logging
import re
import uuid
from typing import Any

from django.db import transaction
from django.utils import timezone

from bookings import state_machine
from bookings.alerts import money_alert
from bookings.models import Booking, BookingEvent, BookingPayout
from core.models import ActorKind
from core.outbox import enqueue
from core.transitions import locked_for_update
from payments.idempotency import idempotency_key
from payments.models import ConnectedAccount
from payments.stripe_client import (
    PAYOUT_ACTION_PREFIX,
    StripeGateway,
    StripeRequestError,
    get_gateway,
)

logger = logging.getLogger(__name__)

S = Booking.Status

PAYOUT_FAILED = "payout_failed"
PAYOUT_FAILED_TOPIC = "booking.payout_failed"

_CODE = re.compile(r"[a-z0-9_]{1,64}")
# A payout can be made, and fail, once the helper's share is theirs: released, paid out,
# partially settled (C), or closed later (a tip, a payout that failed after a few days).
_PAID_OUT_OR_ON_ITS_WAY = (S.RELEASED, S.PAID_OUT, S.PARTIALLY_SETTLED, S.CLOSED)

OPENED_TOPIC = "booking.payout_opened"
CREATED = "payout_created"
PAID = "payout_paid"
REFUSED = "payout_refused"
# Stripe codes worth sending again later: the money isn't available in the helper's
# balance yet (a fresh charge, or Stripe's payout delay for new accounts), too many
# requests, a request with the same key still running, a locked object.
# Money not yet available on the helper's account: Stripe stores this answer under the key.
UNAVAILABLE = "balance_insufficient"
WAIT_CODES = frozenset(
    {"balance_insufficient", "rate_limit", "lock_timeout", "idempotency_key_in_use"}
)
# A payout not sent this long after it was due goes to support (still retried).
SEND_PATIENCE = dt.timedelta(days=7)


def open_payout(booking: Booking, kind: BookingPayout.Kind, amount_cents: int) -> BookingPayout:
    """Record that `amount_cents` is due to the helper of `booking`, locked in this
    transaction, and queue it. Once per booking and kind: a second call returns the first."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("A payout opens inside the transaction that made it due.")
    payout, created = BookingPayout.objects.get_or_create(
        booking=booking,
        kind=kind,
        defaults={"amount_cents": amount_cents, "created_at": timezone.now()},
    )
    if created:
        enqueue(
            OPENED_TOPIC,
            aggregate=booking,
            payload={**_ids(booking), "payout_id": str(payout.pk)},
        )
    return payout


def payout_key(payout: BookingPayout) -> str:
    return idempotency_key(
        "booking",
        str(payout.booking_id),
        "payout",
        payout.kind.lower(),
        version=payout.key_version,
    )


def pay_out(payout_pk: int, *, gateway: StripeGateway | None = None) -> BookingPayout | None:
    """Send the payout to Stripe if it isn't yet (module docstring). Idempotent: safe from
    the outbox handler, Celery retries and the sweep. Raises `StripeRequestError` when it
    is worth trying again later."""
    payout = BookingPayout.objects.select_related("booking").filter(pk=payout_pk).first()
    if payout is None or payout.payout_id or payout.refused_at:
        return payout
    account = _helper_account(payout.booking)
    if account is None:
        _refuse(payout, "no_connected_account")
        return payout
    gateway = gateway or get_gateway()
    kind = payout.kind.lower()
    booking_id = str(payout.booking_id)
    try:
        if payout.sent_at is not None:
            found = gateway.find_payout(booking_id=booking_id, connected_account=account, kind=kind)
            if found:
                return _created(payout, found, looked_up=True)
        else:
            payout = _save(payout, sent_at=timezone.now())
        result = gateway.create_payout(
            booking_id=booking_id,
            connected_account=account,
            amount_cents=payout.amount_cents,
            kind=kind,
            idempotency_key=payout_key(payout),
        )
    except StripeRequestError as exc:
        if (exc.http_status is not None and exc.http_status >= 500) or exc.code == UNAVAILABLE:
            # Stripe stored this answer under the key and would replay it for 24 hours
            # (a 5xx, or money not yet available on the helper's account): look the payout
            # up before the next send, then send under a new version.
            payout = _save(payout, key_version=payout.key_version + 1)
        if _worth_waiting(exc):
            if timezone.now() - payout.created_at >= SEND_PATIENCE:
                _refuse(payout, exc.code or "unsent", keep_trying=True)
            raise
        _refuse(payout, exc.code or "refused")
        return payout
    return _created(payout, result.id, looked_up=False)


def _worth_waiting(exc: StripeRequestError) -> bool:
    return (
        exc.http_status is None
        or exc.http_status >= 500
        or exc.http_status in (409, 429)
        or exc.code in WAIT_CODES
    )


def _created(payout: BookingPayout, stripe_id: str, *, looked_up: bool) -> BookingPayout:
    with locked_for_update(BookingPayout.objects, payout.pk) as locked:
        if locked.payout_id:
            return locked
        locked.payout_id = stripe_id
        locked.save(update_fields=["payout_id", "updated_at"])
        BookingEvent.objects.create(
            booking_id=locked.booking_id,
            type=CREATED,
            actor_kind=ActorKind.SYSTEM,
            data={
                "payout": stripe_id,
                "kind": locked.kind,
                "amount_cents": locked.amount_cents,
                "looked_up": looked_up,
            },
        )
    return BookingPayout.objects.select_related("booking").get(pk=payout.pk)


def _save(payout: BookingPayout, **fields: Any) -> BookingPayout:
    with locked_for_update(BookingPayout.objects, payout.pk) as locked:
        for name, value in fields.items():
            setattr(locked, name, value)
        locked.save(update_fields=[*fields, "updated_at"])
    return BookingPayout.objects.select_related("booking").get(pk=payout.pk)


def _refuse(payout: BookingPayout, code: str, *, keep_trying: bool = False) -> None:
    """Support pays it out by hand. With `keep_trying` (it waited too long) the sweep still
    retries it, and support is told once."""
    data_code = code if _CODE.fullmatch(code) else "unknown"
    with locked_for_update(Booking.objects, payout.booking_id) as booking:
        locked = BookingPayout.objects.select_for_update().get(pk=payout.pk)
        if locked.refused_at or locked.payout_id:
            return
        flagged = BookingEvent.objects.filter(
            booking=booking, type=REFUSED, data__kind=locked.kind
        ).exists()
        if keep_trying and flagged:
            return
        if not keep_trying:
            locked.refused_at = timezone.now()
            locked.save(update_fields=["refused_at", "updated_at"])
        BookingEvent.objects.create(
            booking=booking,
            type=REFUSED,
            actor_kind=ActorKind.SYSTEM,
            data={"kind": locked.kind, "stripe_code": data_code, "retrying": keep_trying},
        )
        state_machine.flag_for_support(booking, REFUSED, {"kind": locked.kind})


def unsent(
    now: dt.datetime | None = None, grace: dt.timedelta = dt.timedelta(minutes=15)
) -> list[int]:
    """Payouts due for longer than `grace` that Stripe hasn't got yet: the sweep queues
    `pay_out` again (a lost broker message, retries that ran out, money not available)."""
    cutoff = (now or timezone.now()) - grace
    return list(
        BookingPayout.objects.filter(payout_id="", refused_at__isnull=True, created_at__lt=cutoff)
        .order_by("created_at")
        .values_list("pk", flat=True)[:100]
    )


def pay_out_after_commit(payout_pk: Any) -> None:
    from bookings.tasks import pay_out_task  # bookings.tasks imports this module

    transaction.on_commit(lambda: pay_out_task.delay(int(payout_pk)))


def record_payout_paid(event: dict[str, Any]) -> Booking | None:
    """Apply a verified payout.paid event. Webhook transaction only.

    Returns the booking, or None when the payout isn't one of a booking's. A payout on
    another account than the helper's is flagged for support. A release payout of a
    RELEASED booking moves it to PAID_OUT; anything else is only recorded.
    """
    payout = event["data"]["object"]
    booking_id = _booking_id_of(payout)
    if booking_id is None:
        return None
    action = str((payout.get("metadata") or {}).get("action") or "")
    kind = action.removeprefix(PAYOUT_ACTION_PREFIX).upper()
    if not action.startswith(PAYOUT_ACTION_PREFIX) or kind not in BookingPayout.Kind.values:
        return None
    try:
        with locked_for_update(Booking.objects, booking_id) as booking:
            ids = {"payout": str(payout.get("id") or ""), "stripe_event": event["id"]}
            if event.get("account") != _helper_account(booking):
                state_machine.flag_for_support(booking, "payout_on_another_account", ids)
                return booking
            row = BookingPayout.objects.select_for_update().filter(booking=booking, kind=kind)
            ours = row.first()
            if ours is not None and ours.paid_at is None:
                if not ours.payout_id:
                    ours.payout_id = ids["payout"]
                ours.paid_at = timezone.now()
                ours.save(update_fields=["payout_id", "paid_at", "updated_at"])
                BookingEvent.objects.create(
                    booking=booking,
                    type=PAID,
                    actor_kind=ActorKind.STRIPE_WEBHOOK,
                    data={**ids, "kind": kind, "amount_cents": ours.amount_cents},
                )
            if kind == BookingPayout.Kind.RELEASE and booking.status == S.RELEASED:
                state_machine.apply_stripe_transition(
                    booking.pk, S.PAID_OUT, stripe_event_id=event["id"], data=ids
                )
                booking.refresh_from_db()
    except Booking.DoesNotExist:
        return None
    return booking


def _ids(booking: Booking) -> dict[str, str]:
    return {"booking_id": str(booking.pk), "task_id": str(booking.task_id)}


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
