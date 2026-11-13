"""Settling a cancelled booking's money at Stripe (hard rules a, b, c and g; PLAN.md,
"TaskBeavo cancellation policy"; .reviews/BEAV-106-partial-refund-handoff.md).

A cancel or withdrawal that moves money writes a `CancellationSettlement` holding the
amounts `bookings/cancellation.py` computed, in the transaction that moves the booking
to CANCEL_REQUESTED (`open_settlement`), with a `booking.settlement_opened` outbox row
whose handler queues `settle` (Celery). No money moves from the client mutation itself:
the Stripe calls run here, server-side, and only Stripe's webhook confirms the result.

`settle(booking_id)` runs the steps not yet done, in this order, with explicit amounts
(never Stripe's proportional `reverse_transfer` or `refund_application_fee`):

1. fee refund Y = max(0, F - K), TaskBeavo → the helper's account
   (`booking:{id}:cancel:fee_refund:v{n}`), skipped when Y = 0;
2. transfer reversal X = R + max(0, K - F), helper → TaskBeavo
   (`...:cancel:transfer_reversal:v{n}`);
3. poster refund R, TaskBeavo → the poster's card (`...:cancel:refund:v{n}`), skipped when
   R = 0 (a nearly finished withdrawal on a credited booking: K takes all that is left).

So TaskBeavo never pays out money it doesn't hold, and the reversal always fits the
helper's balance. It is independent of why the booking is settled (poster cancel,
withdrawal, the no-access call-out of BEAV-125) and refuses to run unless the booking is
in a status whose money is still held at Stripe (`SETTLING`), whatever the case.

Each step is recorded as it succeeds, under a short row lock on the settlement, never
across a Stripe call. Before a step is sent, when it was sent before without an answer
saved (a network error, a 5xx, a worker that died, or a retry past Stripe's 24-hour key
window), it is looked up at Stripe by `metadata.booking_id` and `metadata.action` and
recorded if it went through (review R3-1), so a bumped key never sends it twice. A 5xx
is stored by Stripe under its key, so the step's key version goes up for the next try.
A 4xx no retry changes stops the settlement for support (`refused_at`), with a money
alert: after step 1, TaskBeavo has given the helper F - K and the poster isn't refunded
yet, which support finishes by hand. There is no "resume" action yet (BEAV-157); until
then a manual finish in the Stripe dashboard does the remaining steps in the same order,
the transfer reversal of X before the refund of R, never a refund alone (that would pay
the poster from TaskBeavo's balance, hard rule b). A charge whose transfer or fee isn't
filled in yet is retried for CHARGE_PARTS_PATIENCE before it is refused (review R1-2).

Only a Stripe webhook completes a settlement (hard rule a), in the webhook transaction,
on the event of its last step:

- R > 0: `confirm_refund`, on charge.refunded once Stripe's refunded amount reaches R. The
  refund is only ever sent after the transfer reversal succeeded (step order), so it is
  the step that completes the settlement; transfer.reversed changes nothing then.
- R = 0: `confirm_reversal`, on transfer.reversed once the transfer's reversed amount
  reaches X (user decision 2026-11-07). There is no refund and no charge.refunded.

Either way the booking becomes REFUNDED (a full refund) or PARTIALLY_SETTLED through
`apply_stripe_transition`, and the settlement gets `confirmed_at`, a
`settlement_confirmed` BookingEvent, the poster's EUR 5 credit when the settlement
carries one (a helper's late cancel, `PosterCredit`, funded by TaskBeavo), the share of a
spent credit that comes back, and a `booking.cancellation_settled` outbox row (for
BEAV-117's payout of C, and the poster's and helper's notifications).
"""

import datetime as dt
import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from bookings import cancellation, credit, state_machine
from bookings.alerts import money_alert
from bookings.cancellation import Case, Outcome
from bookings.models import Booking, BookingEvent, CancellationSettlement, PosterCredit
from core.models import ActorKind
from core.outbox import enqueue
from core.transitions import locked_for_update
from payments.idempotency import idempotency_key
from payments.stripe_client import (
    TRANSFER_REVERSAL_ACTION,
    SettlementSplit,
    StripeGateway,
    StripeIdError,
    StripeRequestError,
    get_gateway,
)

logger = logging.getLogger(__name__)

S = Booking.Status

# Statuses whose money is still held at Stripe, from which a settlement may run.
SETTLING = frozenset({S.CANCEL_REQUESTED, S.NO_ACCESS_REPORTED})

OPENED_TOPIC = "booking.settlement_opened"
SETTLED_TOPIC = "booking.cancellation_settled"
STEP_DONE = "settlement_step_done"
REFUSED = "settlement_refused"
CONFIRMED = "settlement_confirmed"
CREDIT_GRANTED = "poster_credit_granted"
# The settlement can't run: the booking isn't in a held status, or has no PaymentIntent.
NOT_SETTLEABLE = "settlement_not_settleable"
# Every step was sent to Stripe, but no webhook confirmed the last one within
# CONFIRMATION_PATIENCE: a webhook endpoint missing charge.refunded or transfer.reversed,
# a delivery Stripe gave up on, or an amount that never matched.
UNCONFIRMED = "settlement_unconfirmed"
CONFIRMATION_PATIENCE = dt.timedelta(hours=24)


def open_settlement(booking: Booking, outcome: Outcome) -> CancellationSettlement:
    """Record the settlement of `outcome` for `booking`, locked in this transaction and
    already moved to a `SETTLING` status, and queue it. No Stripe call here."""
    if not transaction.get_connection().in_atomic_block or booking.status not in SETTLING:
        raise RuntimeError("A settlement opens inside the locked cancel transaction.")
    if outcome.case == Case.NOT_CHARGED:
        raise ValueError("Nothing was charged, so nothing is settled.")
    settlement = CancellationSettlement.objects.create(
        booking=booking,
        case=outcome.case.value,
        work_done_percent=outcome.work_done_percent,
        amount_cents=outcome.amount_cents,
        fee_cents=outcome.fee_cents,
        helper_compensation_cents=outcome.helper_compensation_cents,
        kept_fee_cents=outcome.kept_fee_cents,
        refund_cents=outcome.refund_cents,
        transfer_reversal_cents=outcome.transfer_reversal_cents,
        fee_refund_cents=outcome.fee_refund_cents,
        poster_credit_cents=outcome.poster_credit_cents,
    )
    booking.refund_cents = outcome.refund_cents
    booking.helper_compensation_cents = outcome.helper_compensation_cents
    booking.save(update_fields=["refund_cents", "helper_compensation_cents", "updated_at"])
    enqueue(OPENED_TOPIC, aggregate=booking, payload=_ids(booking))
    return settlement


def outcome_of(settlement: CancellationSettlement) -> Outcome:
    """The stored amounts as an `Outcome`, which checks them again."""
    return Outcome(
        case=Case(settlement.case),
        amount_cents=settlement.amount_cents,
        fee_cents=settlement.fee_cents,
        helper_compensation_cents=settlement.helper_compensation_cents,
        kept_fee_cents=settlement.kept_fee_cents,
        kept_fee_percent=settlement.booking.fee_percent,
        refund_cents=settlement.refund_cents,
        transfer_reversal_cents=settlement.transfer_reversal_cents,
        fee_refund_cents=settlement.fee_refund_cents,
        poster_credit_cents=settlement.poster_credit_cents,
        work_done_percent=settlement.work_done_percent,
    )


@dataclass(frozen=True)
class _Step:
    name: str
    id_field: str
    sent_field: str
    version_field: str
    key_action: str


STEPS = (
    _Step(
        "fee_refund", "fee_refund_id", "fee_refund_sent_at", "fee_refund_key_version", "fee_refund"
    ),
    _Step(
        "transfer_reversal",
        "transfer_reversal_id",
        "transfer_reversal_sent_at",
        "transfer_reversal_key_version",
        "transfer_reversal",
    ),
    _Step("refund", "refund_id", "refund_sent_at", "refund_key_version", "refund"),
)


def step_key(settlement: CancellationSettlement, step: _Step) -> str:
    return idempotency_key(
        "booking",
        str(settlement.booking_id),
        "cancel",
        step.key_action,
        version=getattr(settlement, step.version_field),
    )


def settle(
    booking_id: Any, *, gateway: StripeGateway | None = None
) -> CancellationSettlement | None:
    """Run the settlement's remaining Stripe steps (module docstring). Idempotent: safe to
    run any number of times, from the outbox handler, Celery retries and the sweep.
    Raises `StripeRequestError` on a transient failure, for the caller to retry."""
    settlement = (
        CancellationSettlement.objects.select_related("booking")
        .filter(booking_id=booking_id)
        .first()
    )
    if settlement is None or settlement.confirmed_at or settlement.refused_at:
        return settlement
    if _all_sent(settlement, _split(outcome_of(settlement))):
        return settlement
    booking = settlement.booking
    if booking.status not in SETTLING or not booking.payment_intent_id:
        _not_settleable(settlement)
        return settlement
    split = _split(outcome_of(settlement))
    gateway = gateway or get_gateway()
    try:
        if not settlement.transfer_id:
            parts = gateway.retrieve_charge_parts(
                booking_id=str(booking.pk),
                payment_intent=booking.payment_intent_id,
                expect_application_fee=booking.fee_cents > 0,
            )
            settlement = _save(
                settlement,
                charge_id=parts.charge,
                transfer_id=parts.transfer,
                application_fee_id=parts.application_fee,
            )
        for step in STEPS:
            if getattr(settlement, step.id_field) or _skipped(step, split):
                continue
            # A chargeback or support decision may have moved the booking since.
            if (
                Booking.objects.filter(pk=booking.pk).values_list("status", flat=True).get()
                not in SETTLING
            ):
                _not_settleable(settlement)
                return settlement
            settlement = _run_step(gateway, settlement, step, split)
            if settlement.refused_at:
                return settlement
    except StripeRequestError as exc:
        if _transient(exc) and not _charge_parts_overdue(settlement, exc):
            raise
        _refuse(settlement, "charge" if not settlement.transfer_id else _next_step(settlement), exc)
    except StripeIdError:
        # A stored Stripe ID failed its shape check: retrying can't fix it, support can.
        # Any other ValueError is a bug in the amounts and must surface, not be refused.
        step = "charge" if not settlement.transfer_id else _next_step(settlement)
        _refuse(settlement, step, StripeRequestError(step, code="invalid_stripe_id"))
    return settlement


def _run_step(
    gateway: StripeGateway, settlement: CancellationSettlement, step: _Step, split: SettlementSplit
) -> CancellationSettlement:
    booking_id = str(settlement.booking_id)
    if getattr(settlement, step.sent_field) is not None:
        found = _lookup(gateway, settlement, step)
        if found:
            return _done(settlement, step, found, looked_up=True)
    settlement = _mark_sent(settlement, step)
    send: Callable[[], Any]
    if step.name == "fee_refund":
        send = lambda: gateway.refund_application_fee(  # noqa: E731
            booking_id=booking_id,
            application_fee=settlement.application_fee_id,
            split=split,
            idempotency_key=step_key(settlement, step),
        )
    elif step.name == "transfer_reversal":
        send = lambda: gateway.reverse_transfer(  # noqa: E731
            booking_id=booking_id,
            transfer=settlement.transfer_id,
            split=split,
            fee_refund_id=settlement.fee_refund_id,
            idempotency_key=step_key(settlement, step),
        )
    else:
        send = lambda: gateway.refund_poster(  # noqa: E731
            booking_id=booking_id,
            payment_intent=settlement.booking.payment_intent_id,
            split=split,
            transfer_reversal_id=settlement.transfer_reversal_id,
            idempotency_key=step_key(settlement, step),
        )
    try:
        result = send()
    except StripeRequestError as exc:
        if exc.http_status is not None and exc.http_status >= 500:
            # Stripe stored this failure under the key: the next try takes a new version,
            # after looking the step up (it may have gone through).
            settlement = _save(
                settlement, **{step.version_field: getattr(settlement, step.version_field) + 1}
            )
        raise
    return _done(settlement, step, result.id, looked_up=False)


# Stripe answers these 4xx for a request worth trying again: too many requests, a request
# with the same key still running, or a locked object.
TRANSIENT_STATUSES = frozenset({409, 429})
TRANSIENT_CODES = frozenset({"rate_limit", "lock_timeout", "idempotency_key_in_use"})


# Stripe fills a destination charge's transfer and application fee a few seconds after
# the payment succeeds (seen in test mode, review R1-2): until then retrieve_charge_parts
# answers these. Past CHARGE_PARTS_PATIENCE they are for good, and go to support.
CHARGE_PARTS_CODES = frozenset({"no_charge", "not_a_destination_charge"})
CHARGE_PARTS_PATIENCE = dt.timedelta(hours=1)


def _charge_parts_overdue(settlement: CancellationSettlement, exc: StripeRequestError) -> bool:
    return (
        exc.code in CHARGE_PARTS_CODES
        and timezone.now() - settlement.created_at >= CHARGE_PARTS_PATIENCE
    )


def _transient(exc: StripeRequestError) -> bool:
    """A failure for the Celery retry, not support: network, 5xx, or a transient 4xx."""
    return (
        exc.http_status is None
        or exc.http_status >= 500
        or exc.http_status in TRANSIENT_STATUSES
        or exc.code in TRANSIENT_CODES
    )


def _lookup(gateway: StripeGateway, settlement: CancellationSettlement, step: _Step) -> str | None:
    booking_id = str(settlement.booking_id)
    if step.name == "fee_refund":
        return gateway.find_fee_refund(
            booking_id=booking_id, application_fee=settlement.application_fee_id
        )
    if step.name == "transfer_reversal":
        return gateway.find_transfer_reversal(
            booking_id=booking_id, transfer=settlement.transfer_id
        )
    return gateway.find_poster_refund(
        booking_id=booking_id, payment_intent=settlement.booking.payment_intent_id
    )


def _mark_sent(settlement: CancellationSettlement, step: _Step) -> CancellationSettlement:
    if getattr(settlement, step.sent_field) is not None:
        return settlement
    return _save(settlement, **{step.sent_field: timezone.now()})


def _done(
    settlement: CancellationSettlement, step: _Step, stripe_id: str, *, looked_up: bool
) -> CancellationSettlement:
    with locked_for_update(CancellationSettlement.objects, settlement.pk) as locked:
        if getattr(locked, step.id_field):
            return locked
        setattr(locked, step.id_field, stripe_id)
        locked.save(update_fields=[step.id_field, "updated_at"])
        BookingEvent.objects.create(
            booking_id=locked.booking_id,
            type=STEP_DONE,
            actor_kind=ActorKind.SYSTEM,
            data={"step": step.name, "stripe_id": stripe_id, "looked_up": looked_up},
        )
    return CancellationSettlement.objects.select_related("booking").get(pk=settlement.pk)


def _save(settlement: CancellationSettlement, **fields: Any) -> CancellationSettlement:
    with locked_for_update(CancellationSettlement.objects, settlement.pk) as locked:
        for name, value in fields.items():
            setattr(locked, name, value)
        locked.save(update_fields=[*fields, "updated_at"])
    return CancellationSettlement.objects.select_related("booking").get(pk=settlement.pk)


def _skipped(step: _Step, split: SettlementSplit) -> bool:
    """No fee refund when there is no fee to give back: a TaskBeavo credit covered the
    fee, or K took all of it (bookings/credit.py, BEAV-123). No poster refund when K
    took all that was left of the charge (R = 0): transfer.reversed confirms then."""
    return (step.name == "fee_refund" and split.fee_refund_cents == 0) or (
        step.name == "refund" and split.refund_cents == 0
    )


def _all_sent(settlement: CancellationSettlement, split: SettlementSplit) -> bool:
    return all(getattr(settlement, s.id_field) or _skipped(s, split) for s in STEPS)


def _next_step(settlement: CancellationSettlement) -> str:
    split = _split(outcome_of(settlement))
    return next(
        (s.name for s in STEPS if not getattr(settlement, s.id_field) and not _skipped(s, split)),
        "refund",
    )


def _refuse(settlement: CancellationSettlement, step: str, exc: StripeRequestError) -> None:
    """Stripe refused a step for good: support finishes the settlement by hand."""
    with locked_for_update(Booking.objects, settlement.booking_id) as booking:
        locked = CancellationSettlement.objects.select_for_update().get(pk=settlement.pk)
        if locked.refused_at:
            return
        locked.refused_at = timezone.now()
        locked.refused_step = step
        locked.save(update_fields=["refused_at", "refused_step", "updated_at"])
        BookingEvent.objects.create(
            booking=booking,
            type=REFUSED,
            actor_kind=ActorKind.SYSTEM,
            data={"step": step, "stripe_code": _code_key(exc.code)},
        )
        state_machine.flag_for_support(booking, REFUSED, {"step": step})
    money_alert(
        "bookings: Stripe refused a cancellation's settlement step; support must finish it",
        booking_id=settlement.booking_id,
        reason=f"{REFUSED}:{step}",
        stripe_error_code=_code_key(exc.code),
    )


def _not_settleable(settlement: CancellationSettlement) -> None:
    with locked_for_update(Booking.objects, settlement.booking_id) as booking:
        seen = BookingEvent.objects.filter(booking=booking, type=NOT_SETTLEABLE).exists()
        if seen or booking.status in (S.REFUNDED, S.PARTIALLY_SETTLED, S.CLOSED):
            return
        BookingEvent.objects.create(
            booking=booking,
            type=NOT_SETTLEABLE,
            actor_kind=ActorKind.SYSTEM,
            data={"status": booking.status},
        )
        state_machine.flag_for_support(booking, NOT_SETTLEABLE, {"status": booking.status})


def confirm_refund(charge: dict[str, Any], *, stripe_event_id: str) -> Booking | None:
    """Apply a verified charge.refunded event to a settlement. Webhook transaction only.

    Returns the booking it settled or flagged, or None when the charge isn't a settling
    booking's (a late-payment refund, another platform object). The refunded amount must
    reach exactly R: less waits for the next event, more is flagged for support.
    """
    intent = charge.get("payment_intent")
    if not isinstance(intent, str) or not intent:
        return None
    booking_pk = (
        CancellationSettlement.objects.filter(booking__payment_intent_id=intent)
        .values_list("booking_id", flat=True)
        .first()
    )
    if booking_pk is None:
        return None
    with locked_for_update(Booking.objects, booking_pk) as booking:
        settlement = CancellationSettlement.objects.select_for_update().get(booking=booking)
        if settlement.confirmed_at:
            return booking
        refunded = charge.get("amount_refunded")
        if not isinstance(refunded, int) or refunded < settlement.refund_cents:
            return booking
        ids = {"stripe_event": stripe_event_id, "payment_intent": intent}
        if refunded > settlement.refund_cents:
            state_machine.flag_for_support(booking, "settlement_refund_mismatch", ids)
            return booking
        return _complete(
            booking,
            settlement,
            stripe_event_id=stripe_event_id,
            data={"payment_intent": intent, "case": settlement.case},
        )


def confirm_reversal(
    transfer: dict[str, Any], *, stripe_event_id: str, idempotency_key: str | None
) -> Booking | None:
    """Apply a verified transfer.reversed event to a settlement. Webhook transaction only.

    Confirms only a settlement with no poster refund (R = 0), whose transfer reversal is
    its last step; with R > 0 charge.refunded confirms (module docstring). The event's
    transfer must be the settlement's, and the reversal TaskBeavo's for that booking: its
    `metadata.booking_id` and `metadata.action`, or the request's idempotency key. The
    reversed amount must reach exactly X: less waits, more is flagged for support. It may
    arrive before `settle` saved the reversal's ID (the webhook won the race), so the ID
    is recorded here when missing and `settle` then sends nothing more.

    Returns the booking it settled or flagged, or None when the transfer isn't a settling
    booking's or the reversal isn't TaskBeavo's settlement step.
    """
    transfer_id = transfer.get("id")
    if not isinstance(transfer_id, str) or not transfer_id:
        return None
    named = {*_reversals_by_booking(transfer), *_step_key_booking(idempotency_key)}
    booking_pk = (
        CancellationSettlement.objects.filter(transfer_id=transfer_id, booking_id__in=named)
        .values_list("booking_id", flat=True)
        .first()
    )
    if booking_pk is None:
        return None
    reversal_id = _reversals_by_booking(transfer).get(str(booking_pk))
    with locked_for_update(Booking.objects, booking_pk) as booking:
        settlement = CancellationSettlement.objects.select_for_update().get(booking=booking)
        if settlement.confirmed_at or settlement.refund_cents:
            return booking
        reversed_cents = transfer.get("amount_reversed")
        expected = settlement.transfer_reversal_cents
        if not isinstance(reversed_cents, int) or reversed_cents < expected:
            return booking
        ids = {"stripe_event": stripe_event_id, "transfer": transfer_id}
        if reversed_cents > expected:
            state_machine.flag_for_support(booking, "settlement_reversal_mismatch", ids)
            return booking
        if reversal_id and not settlement.transfer_reversal_id:
            settlement.transfer_reversal_id = reversal_id
            settlement.save(update_fields=["transfer_reversal_id", "updated_at"])
            BookingEvent.objects.create(
                booking=booking,
                type=STEP_DONE,
                actor_kind=ActorKind.STRIPE_WEBHOOK,
                data={"step": "transfer_reversal", "stripe_id": reversal_id, "looked_up": False},
            )
        return _complete(
            booking,
            settlement,
            stripe_event_id=stripe_event_id,
            data={"transfer": transfer_id, "case": settlement.case},
        )


def _reversals_by_booking(transfer: dict[str, Any]) -> dict[str, str]:
    """TaskBeavo's settlement reversals among the transfer's, by the booking their metadata
    names (`settle` sets it): booking ID -> reversal ID."""
    reversals = transfer.get("reversals")
    items = reversals.get("data") if isinstance(reversals, dict) else None
    found: dict[str, str] = {}
    for item in items if isinstance(items, list) else []:
        metadata = item.get("metadata") if isinstance(item, dict) else None
        if (
            isinstance(metadata, dict)
            and metadata.get("action") == TRANSFER_REVERSAL_ACTION
            and _is_uuid(metadata.get("booking_id"))
            and isinstance(item.get("id"), str)
        ):
            found[metadata["booking_id"]] = item["id"]
    return found


# The idempotency key of a booking's transfer reversal step (`step_key`), any version.
_REVERSAL_KEY = re.compile(
    rf"booking:([0-9a-f-]{{36}}):cancel:{TRANSFER_REVERSAL_ACTION}:v[1-9][0-9]*"
)


def _step_key_booking(key: str | None) -> list[str]:
    match = _REVERSAL_KEY.fullmatch(key) if isinstance(key, str) else None
    return [match.group(1)] if match and _is_uuid(match.group(1)) else []


def _is_uuid(value: object) -> bool:
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _complete(
    booking: Booking,
    settlement: CancellationSettlement,
    *,
    stripe_event_id: str,
    data: dict[str, Any],
) -> Booking:
    """Settle the locked booking once Stripe confirmed its settlement's last step."""
    target = S.REFUNDED if outcome_of(settlement).is_full_refund else S.PARTIALLY_SETTLED
    moved = state_machine.apply_stripe_transition(
        booking.pk, target, stripe_event_id=stripe_event_id, data=data
    )
    if moved is None:
        return booking
    settlement.confirmed_at = timezone.now()
    settlement.save(update_fields=["confirmed_at", "updated_at"])
    BookingEvent.objects.create(
        booking=moved,
        type=CONFIRMED,
        actor_kind=ActorKind.STRIPE_WEBHOOK,
        # TODO(BEAV-128): when kept_fee_cents > 0 (LATE, NO_ACCESS_CALL_OUT,
        # WITHDRAWAL_PRO_RATA) TaskBeavo has earned K without a release: issue its fee
        # receipt here (rule f), and a storno for any fee receipt already issued.
        data={
            "case": settlement.case,
            "refund_cents": settlement.refund_cents,
            "helper_compensation_cents": settlement.helper_compensation_cents,
            "kept_fee_cents": settlement.kept_fee_cents,
            "transfer_reversal_cents": settlement.transfer_reversal_cents,
            "stripe_event": stripe_event_id,
        },
    )
    if settlement.poster_credit_cents:
        _grant_credit(moved, settlement)
    # The whole credit comes back, in the same transaction as the outcome (rule g, user
    # decision 2026-11-07): where K is kept, TaskBeavo took it in cash in the reversal.
    credit.restore(
        moved,
        timezone.now(),
        reason=f"cancellation:{settlement.case.lower()}",
        cents=cancellation.credit_back_cents(outcome_of(settlement), moved.credit_cents),
    )
    enqueue(SETTLED_TOPIC, aggregate=moved, payload=_ids(moved))
    return moved


def _grant_credit(booking: Booking, settlement: CancellationSettlement) -> None:
    """The EUR 5 goodwill credit of a helper's late cancel (bookings/helper_cancel.py),
    funded by TaskBeavo, granted once Stripe confirmed the poster's full refund."""
    credit, created = PosterCredit.objects.get_or_create(
        source_booking=booking,
        defaults={
            "poster_id": booking.poster_id,
            "amount_cents": settlement.poster_credit_cents,
            "granted_at": timezone.now(),
        },
    )
    if created:
        BookingEvent.objects.create(
            booking=booking,
            type=CREDIT_GRANTED,
            actor_kind=ActorKind.STRIPE_WEBHOOK,
            data={"credit_id": credit.pk, "amount_cents": credit.amount_cents},
        )


def unsettled(
    now: dt.datetime | None = None, grace: dt.timedelta = dt.timedelta(minutes=15)
) -> list[uuid.UUID]:
    """Bookings whose settlement still has steps to send, older than `grace`: the sweep
    queues `settle` again for them (a lost broker message, retries that ran out)."""
    cutoff = (now or timezone.now()) - grace
    return list(
        CancellationSettlement.objects.filter(
            confirmed_at__isnull=True,
            refused_at__isnull=True,
            refund_id="",
            created_at__lt=cutoff,
            booking__status__in=SETTLING,
        )
        # R = 0: the transfer reversal is the last step, transfer.reversed confirms it.
        .exclude(refund_cents=0, transfer_reversal_id__gt="")
        .order_by("created_at")
        .values_list("booking_id", flat=True)[:100]
    )


def flag_unconfirmed(now: dt.datetime | None = None) -> int:
    """Beat task body: flag support once (with a money alert, IDs only) for each settlement
    whose last step, the refund or, with R = 0, the transfer reversal, was sent to Stripe
    over CONFIRMATION_PATIENCE ago without a webhook confirming it. Returns how many."""
    cutoff = (now or timezone.now()) - CONFIRMATION_PATIENCE
    waiting = (
        CancellationSettlement.objects.filter(
            # Sent, whether or not Stripe's answer reached us (a step retried for a day
            # without success shows up here too).
            Q(refund_cents__gt=0, refund_sent_at__lte=cutoff)
            | Q(refund_cents=0, transfer_reversal_sent_at__lte=cutoff),
            confirmed_at__isnull=True,
            refused_at__isnull=True,
            booking__status__in=SETTLING,
        )
        .exclude(
            booking__events__type=state_machine.SUPPORT_FLAG,
            booking__events__data__reason=UNCONFIRMED,
        )
        .order_by("created_at")
        .values_list("booking_id", "refund_cents")[:100]
    )
    flagged = 0
    for booking_id, refund_cents in waiting:
        try:
            with locked_for_update(Booking.objects, booking_id) as booking:
                settlement = CancellationSettlement.objects.select_for_update().get(booking=booking)
                seen = BookingEvent.objects.filter(
                    booking=booking, type=state_machine.SUPPORT_FLAG, data__reason=UNCONFIRMED
                ).exists()
                if (
                    seen
                    or settlement.confirmed_at
                    or settlement.refused_at
                    or booking.status not in SETTLING
                ):
                    continue
                last = "refund" if refund_cents else "transfer_reversal"
                state_machine.flag_for_support(booking, UNCONFIRMED, {"step": last})
        except Exception:
            # One booking failing (a lock timeout) mustn't stop the others; the next run
            # tries it again.
            logger.exception(
                "bookings: flagging an unconfirmed settlement failed",
                extra={"booking.id": str(booking_id)},
            )
            continue
        flagged += 1
    return flagged


def settle_after_commit(booking_id: Any) -> None:
    """Queue `settle` once the current transaction commits."""
    from bookings.tasks import settle_cancellation_task  # bookings.tasks imports this module

    transaction.on_commit(lambda: settle_cancellation_task.delay(str(booking_id)))


def _split(outcome: Outcome) -> SettlementSplit:
    return SettlementSplit(
        charge_cents=outcome.charge_cents,
        application_fee_cents=outcome.fee_cents,
        helper_compensation_cents=outcome.helper_compensation_cents,
        fee_kept_cents=outcome.kept_fee_cents,
        refund_cents=outcome.refund_cents,
        transfer_reversal_cents=outcome.transfer_reversal_cents,
        fee_refund_cents=outcome.fee_refund_cents,
    )


def _code_key(code: str) -> str:
    return code if code and code.replace("_", "").isalnum() and len(code) <= 64 else "unknown"


def _ids(booking: Booking) -> dict[str, str]:
    return {"booking_id": str(booking.pk), "task_id": str(booking.task_id)}
