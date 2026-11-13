"""The booking state machine (docs/PLAN.md, "State machine for task, booking and payment").

Nothing else assigns `Booking.status`. Every change, creation included, runs in one
transaction that locks the booking row (`select_for_update`), checks the move against
`TRANSITIONS`, saves the booking and writes a `BookingEvent` and a `booking.updated`
outbox row (hard rule c). A second caller racing the first (a double tap, a webhook
racing a mutation) waits on the lock, then sees the new status and is refused.

Who may make a move is part of the table:

- POSTER and HELPER: only the booking's own poster or helper (`transition`).
- ADMIN: a staff user deciding in the admin console.
- SYSTEM: Celery Beat sweeps and outbox handlers (timeouts, no reply in 48 h, closing).
- STRIPE_WEBHOOK: every money-confirmed status (FUNDS_HELD, REFUNDED, PARTIALLY_SETTLED,
  PAID_OUT) and chargebacks. Only `apply_stripe_transition` makes these moves, and only
  inside the webhook transaction that recorded the Stripe event in `stripe_events`
  (hard rule a): a client mutation has no such event, so it can never confirm money.
  `transition` refuses the STRIPE_WEBHOOK actor outright.

Out-of-order webhooks: `apply_stripe_transition` returns None, records nothing and
leaves the booking alone when it is already in or past the target, so a late or
replayed event is a no-op instead of an error that makes Stripe retry for days. A move
that fits neither (a payment confirmed on a failed checkout, a chargeback after the
payout) is flagged for support instead (`SUPPORT_FLAG`): a BookingEvent and a Sentry
alert, the booking left alone, and the webhook answers 200, since Stripe redelivering
the event for days would change nothing.

Stripe calls never happen here: callers make them outside the lock, before or after,
with keys from `payments.idempotency` (BEAV-109, BEAV-110, BEAV-108).
"""

import datetime as dt
import uuid
from collections.abc import Mapping
from typing import Any

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.business import is_business_poster
from accounts.models import User
from areas.services import assert_in_service_area
from bookings import credit
from bookings.alerts import money_alert
from bookings.models import Booking, BookingEvent, CancellationSettlement, NoFaultRequest
from bookings.money import fee_schedule_at
from bookings.queries import active, task_is_booked
from care_contacts.models import CareContact
from core.errors import ApiError, ErrorCode
from core.models import ActorKind
from core.outbox import enqueue
from core.transitions import locked_for_update
from helpers.models import HelperProfile
from offers.models import Offer, OfferEvent
from offers.services import task_ends_at, withdraw_registered_only
from payments.models import StripeEvent
from staff import retention as staff_retention
from tasks.models import Task
from uploads.services import lift_legal_hold, place_legal_hold

S = Booking.Status
A = ActorKind

UPDATED_TOPIC = "booking.updated"
# Live events to the helper whose offer was declined, or reopened by a failed checkout.
# Named under booking.*: the offers app's own topics are registered in offers.
OFFER_DECLINED_TOPIC = "booking.offer_declined"
OFFER_REOPENED_TOPIC = "booking.offer_reopened"
# The poster changed the task's access details during an active booking.
ACCESS_DETAILS_TOPIC = "booking.access_details_updated"

# `Booking.cancel_reason` of a withdrawal from the contract (bookings.cancel).
WITHDRAWAL_CANCEL_REASON = "WITHDRAWAL"
# A no-show the poster reported (bookings/arrival.py): only support, the system or Stripe
# move it on, never the poster or the helper.
NO_SHOW_CANCEL_REASON = "NO_SHOW"

# The BookingEvent type of a Stripe event support must look at (`apply_stripe_transition`).
SUPPORT_FLAG = "support_flag"

# A Stripe chargeback (charge.dispute.created) can arrive while the money is held.
_CHARGEBACK = A.STRIPE_WEBHOOK

# (from, to) -> who may make the move. Moves not listed are illegal.
TRANSITIONS: Mapping[tuple[S, S], frozenset[A]] = {
    (S.AWAITING_PAYMENT, S.FUNDS_HELD): frozenset({A.STRIPE_WEBHOOK}),
    # Payment failed (webhook), checkout abandoned or the offer withdrawn during
    # checkout (system, end_checkout_for_withdrawal).
    (S.AWAITING_PAYMENT, S.PAYMENT_FAILED): frozenset({A.STRIPE_WEBHOOK, A.SYSTEM}),
    # Cancelled over 24 h before the start, within 1 h of accepting, or 14 days inactive.
    (S.FUNDS_HELD, S.REFUNDED): frozenset({A.STRIPE_WEBHOOK}),
    # Late, no-fault or helper cancellation; a no-show the poster confirmed or left open.
    (S.FUNDS_HELD, S.CANCEL_REQUESTED): frozenset({A.POSTER, A.HELPER, A.SYSTEM}),
    (S.FUNDS_HELD, S.NO_ACCESS_REPORTED): frozenset({A.HELPER}),
    (S.FUNDS_HELD, S.PAYMENT_REQUESTED): frozenset({A.HELPER}),
    (S.FUNDS_HELD, S.DISPUTED): frozenset({A.POSTER, _CHARGEBACK}),
    (S.FUNDS_HELD, S.INCIDENT_PAUSED): frozenset({A.POSTER, A.HELPER}),
    (S.CANCEL_REQUESTED, S.REFUNDED): frozenset({A.STRIPE_WEBHOOK}),
    (S.CANCEL_REQUESTED, S.PARTIALLY_SETTLED): frozenset({A.STRIPE_WEBHOOK}),
    (S.CANCEL_REQUESTED, S.DISPUTED): frozenset({A.POSTER, A.HELPER, _CHARGEBACK}),
    # The request is withdrawn, by whoever made it (checked in `_check_actor`), or support
    # rejects a poster's no-fault reason after the start, when no cancel is left to make
    # (bookings/no_fault.py).
    (S.CANCEL_REQUESTED, S.FUNDS_HELD): frozenset({A.POSTER, A.HELPER, A.ADMIN}),
    # The poster reschedules, no fee.
    (S.NO_ACCESS_REPORTED, S.FUNDS_HELD): frozenset({A.POSTER}),
    # Call-out accepted or no reply in 48 h: confirmed once the partial refund is.
    (S.NO_ACCESS_REPORTED, S.PARTIALLY_SETTLED): frozenset({A.STRIPE_WEBHOOK}),
    (S.NO_ACCESS_REPORTED, S.DISPUTED): frozenset({A.POSTER, _CHARGEBACK}),
    (S.INCIDENT_PAUSED, S.DISPUTED): frozenset({A.ADMIN, _CHARGEBACK}),
    # The poster taps "Release payment", or support releases on their behalf.
    (S.PAYMENT_REQUESTED, S.RELEASED): frozenset({A.POSTER, A.ADMIN}),
    (S.PAYMENT_REQUESTED, S.PARTIALLY_SETTLED): frozenset({A.STRIPE_WEBHOOK}),
    (S.PAYMENT_REQUESTED, S.DISPUTED): frozenset({A.POSTER, _CHARGEBACK}),
    # 30 days with no release and no dispute.
    (S.PAYMENT_REQUESTED, S.REFUNDED): frozenset({A.STRIPE_WEBHOOK}),
    (S.DISPUTED, S.RELEASED): frozenset({A.ADMIN}),
    (S.DISPUTED, S.REFUNDED): frozenset({A.STRIPE_WEBHOOK}),
    (S.DISPUTED, S.PARTIALLY_SETTLED): frozenset({A.STRIPE_WEBHOOK}),
    # payout.paid on the helper's connected account.
    (S.RELEASED, S.PAID_OUT): frozenset({A.STRIPE_WEBHOOK}),
    # The 7-day blind review window ended.
    (S.PAID_OUT, S.CLOSED): frozenset({A.SYSTEM}),
    (S.PARTIALLY_SETTLED, S.CLOSED): frozenset({A.SYSTEM}),
    (S.REFUNDED, S.CLOSED): frozenset({A.SYSTEM}),
}

# Statuses only Stripe can confirm (hard rule a).
MONEY_CONFIRMED = frozenset({S.FUNDS_HELD, S.REFUNDED, S.PARTIALLY_SETTLED, S.PAID_OUT})

# How far along each status is, for ignoring out-of-order webhooks: an event whose target
# ranks below the booking's current status is stale. PAYMENT_FAILED is not "past"
# anything: a payment that succeeds after checkout failed must reach a human
# (`_is_stale`), and so must a chargeback on a booking that already settled.
_RANK: Mapping[S, int] = {
    S.AWAITING_PAYMENT: 0,
    S.FUNDS_HELD: 1,
    S.CANCEL_REQUESTED: 2,
    S.NO_ACCESS_REPORTED: 2,
    S.INCIDENT_PAUSED: 2,
    S.PAYMENT_REQUESTED: 2,
    S.DISPUTED: 3,
    S.RELEASED: 4,
    S.PARTIALLY_SETTLED: 5,
    S.REFUNDED: 5,
    S.PAID_OUT: 5,
    S.PAYMENT_FAILED: 6,
    S.CLOSED: 6,
}


def is_allowed(source: S | str, target: S | str, actor_kind: A | str) -> bool:
    """Whether `actor_kind` may move a booking from `source` to `target`."""
    return A(actor_kind) in TRANSITIONS.get((S(source), S(target)), frozenset())


def create_booking(
    poster: User,
    offer: Offer,
    *,
    expected_price_cents: int,
    expected_total_cents: int,
    expected_fee_schedule_version: str,
    slot_start: dt.datetime | None = None,
    now: dt.datetime | None = None,
) -> Booking:
    """Create the booking for `poster` accepting `offer`, AWAITING_PAYMENT.

    The offer must be SENT: one marked NEEDS_UPDATE waits for the helper to confirm it
    (OFFER_NEEDS_UPDATE). `expected_price_cents` is the price the checkout showed the
    poster; if the helper changed it since, nothing is booked (OFFER_CHANGED).
    `expected_total_cents` is the total on the pay button; if the fee schedule in force
    changed since (a new price list took effect), nothing is booked (FEE_CHANGED); the same
    when `expected_fee_schedule_version`, the list the checkout's fee notice quoted, is no
    longer the one in force, even if the total is equal (a rate change at the minimum fee
    keeps the total but not the rate K uses; review R3-2). The fee
    is always computed here, from the schedule in force now, which the booking keeps
    (`fee_schedule_version`, `fee_percent`), less the poster's TaskBeavo credit
    (`credit_cents`, bookings/credit.py), which the expected total includes. The offer
    becomes ACCEPTED and the task's other live offers DECLINED, each with an OfferEvent
    and the declined ones with an outbox row; a failed checkout reopens them all
    (`_reopen_offers`).

    acceptOffer (BEAV-109) calls this, then creates the PaymentIntent outside this
    transaction. The task's location is checked against the active service areas again
    (hard rule j). A fixed-date task starts at its window start; a flexible-date task
    needs `slot_start`, the agreed day, between now and the end of its open period
    (INVALID_TASK_DATES otherwise): the late-cancel window (rule g), the contact's edit
    lock and the no-show timers all run from it. No money moves here.

    A business poster can't book a private helper (BUSINESS_POSTER_REGISTERED_ONLY,
    accounts.business), checked under the poster's lock, which becoming a business takes too.

    Locks: the poster, then the task's on-site contact, then the task, then the offer.
    Contact deletion locks the contact before the bookings and tasks using it, and becoming
    a business locks the poster before their tasks and offers, so none of them deadlock.
    """
    with transaction.atomic():
        User.objects.select_for_update(no_key=True).only("pk").get(pk=poster.pk)
        contact_id = (
            Task.objects.filter(pk=offer.task_id).values_list("care_contact_id", flat=True).first()
        )
        _lock_contact(contact_id)
        task = Task.objects.select_for_update().get(pk=offer.task_id)
        if task.care_contact_id != contact_id:
            # The poster changed the contact in between: lock the new one too.
            _lock_contact(task.care_contact_id)
        offer = Offer.objects.select_for_update().get(pk=offer.pk)
        if task.poster_id != poster.pk:
            raise ApiError(ErrorCode.OFFER_NOT_FOUND, "No such offer on your task.")
        if task_is_booked(task):
            raise ApiError(ErrorCode.TASK_ALREADY_BOOKED, "This task already has a booking.")
        now = now or timezone.now()
        if task.status != Task.Status.OPEN or not offer.is_live or offer.expires_at <= now:
            raise ApiError(ErrorCode.OFFER_NOT_EDITABLE, "The offer is no longer open.")
        if offer.status == Offer.Status.NEEDS_UPDATE:
            raise ApiError(ErrorCode.OFFER_NEEDS_UPDATE, "The helper hasn't confirmed the offer.")
        poster_is_business = is_business_poster(poster)
        if poster_is_business and not _helper_is_trader(offer):
            raise ApiError(
                ErrorCode.BUSINESS_POSTER_REGISTERED_ONLY,
                "A business poster books registered helpers only.",
            )
        if offer.price_cents != expected_price_cents:
            raise ApiError(ErrorCode.OFFER_CHANGED, "The offer's price changed; check it again.")
        if task.location is None:
            raise ApiError(ErrorCode.OUT_OF_SERVICE_AREA, "The task has no location.")
        schedule = fee_schedule_at(now)
        fee = schedule.fee_cents(offer.price_cents)
        # The poster's TaskBeavo credit comes off the fee (bookings/credit.py); the total
        # on the pay button already shows it.
        allocation = credit.take(poster, fee)
        fee -= allocation.total_cents
        if (
            offer.price_cents + fee != expected_total_cents
            or schedule.version != expected_fee_schedule_version
        ):
            raise ApiError(ErrorCode.FEE_CHANGED, "The service fee changed; check the total.")
        assert_in_service_area(task.location)
        start = _slot_start(task, slot_start, now)
        booking = Booking.objects.create(
            task=task,
            offer=offer,
            poster=poster,
            helper_id=offer.helper_id,
            care_contact_id=task.care_contact_id,
            amount_cents=offer.price_cents,
            fee_cents=fee,
            credit_cents=allocation.total_cents,
            fee_schedule_version=schedule.version,
            fee_percent=schedule.percent,
            slot_start=start,
            status_changed_at=now,
            poster_was_business=poster_is_business,
        )
        credit.record(booking, allocation, now)
        _accept_offers(booking, offer, poster, now)
        _record(booking, None, S.AWAITING_PAYMENT, A.POSTER, poster, {"offer_id": str(offer.pk)})
    return booking


def lock_poster(poster_id: int | None) -> None:
    """Lock the poster's row first, before their bookings, tasks and offers: the order
    acceptOffer, the offers services and becoming a business poster use (BEAV-195). FOR NO
    KEY UPDATE, so rows referencing the poster can still be inserted meanwhile."""
    if poster_id is not None:
        User.objects.select_for_update(no_key=True).only("pk").filter(pk=poster_id).first()


def _helper_is_trader(offer: Offer) -> bool:
    return HelperProfile.objects.filter(
        user_id=offer.helper_id, declared_status=HelperProfile.Status.BUSINESS
    ).exists()


def _accept_offers(booking: Booking, accepted: Offer, poster: User, now: dt.datetime) -> None:
    """The accepted offer becomes ACCEPTED, the task's other live offers DECLINED."""
    accepted.status = Offer.Status.ACCEPTED
    accepted.save(update_fields=["status", "updated_at"])
    _offer_event(accepted, OfferEvent.Type.ACCEPTED, A.POSTER, poster, booking)
    others = list(
        Offer.objects.select_for_update()
        .filter(task_id=booking.task_id, status__in=Offer.LIVE)
        .exclude(pk=accepted.pk)
        .order_by("pk")
    )
    for other in others:
        other.status = Offer.Status.DECLINED
        other.closed_at = now
        other.save(update_fields=["status", "closed_at", "updated_at"])
        _offer_event(other, OfferEvent.Type.DECLINED, A.POSTER, poster, booking)
        enqueue(OFFER_DECLINED_TOPIC, aggregate=other, payload=_offer_payload(other, booking))
    booking.declined_offer_ids = [other.pk for other in others]
    booking.save(update_fields=["declined_offer_ids"])


def _reopen_offers(booking: Booking, now: dt.datetime, *, accepted: bool = True) -> list[Offer]:
    """A failed checkout: the accepted offer and the ones it declined are live again, as
    SENT, or NEEDS_UPDATE if the task changed under them; one whose expiry passed
    meanwhile is EXPIRED (the helper may renew it). If the poster is a business poster by
    now, private helpers' offers are withdrawn instead (BEAV-195) and not returned. Callers
    hold the poster's lock (`lock_poster`), taken before the booking and the task, so this
    can't race the switch to business. Without `accepted` (a task re-opened
    after its helper cancelled, `reopen_declined_offers`) the accepted offer stays closed.
    Returns the offers it touched."""
    declined = Q(pk__in=booking.declined_offer_ids, status=Offer.Status.DECLINED)
    chosen = Q(pk=booking.offer_id, status=Offer.Status.ACCEPTED)
    offers = list(
        Offer.objects.select_for_update()
        .filter(declined | chosen if accepted else declined)
        .order_by("pk")
    )
    if is_business_poster(booking.poster_id):
        # The poster became a business since (accounts.business): a private helper's offer
        # is withdrawn with the neutral reason instead, and gets no "open again" notice.
        private = [offer for offer in offers if not _helper_is_trader(offer)]
        for offer in private:
            withdraw_registered_only(offer, now)
        offers = [offer for offer in offers if offer not in private]
    for offer in offers:
        if offer.expires_at <= now:
            offer.status = Offer.Status.EXPIRED
            offer.closed_at = now
        else:
            changed = offer.task_changed
            offer.status = Offer.Status.NEEDS_UPDATE if changed else Offer.Status.SENT
            offer.closed_at = None
        offer.save(update_fields=["status", "closed_at", "updated_at"])
        _offer_event(offer, OfferEvent.Type.REOPENED, A.SYSTEM, None, booking)
        enqueue(OFFER_REOPENED_TOPIC, aggregate=offer, payload=_offer_payload(offer, booking))
    return offers


def reopen_declined_offers(booking: Booking, now: dt.datetime) -> list[Offer]:
    """The poster re-opened the task of `booking`, locked with its task in this
    transaction (bookings/helper_cancel.py): the offers its acceptance declined are live
    again, the cancelled helper's stays closed. Returns the offers it touched."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Offers reopen inside the locked re-open transaction.")
    return _reopen_offers(booking, now, accepted=False)


def _offer_event(
    offer: Offer, type: OfferEvent.Type, kind: A, actor: User | None, booking: Booking
) -> None:
    OfferEvent.objects.create(
        offer=offer,
        type=type,
        actor_kind=kind,
        actor=actor,
        data={"task_id": str(offer.task_id), "booking_id": str(booking.pk)},
    )


def _offer_payload(offer: Offer, booking: Booking) -> dict[str, str]:
    return {
        "offer_id": str(offer.pk),
        "task_id": str(offer.task_id),
        "booking_id": str(booking.pk),
    }


def lock_checkout(offer_id: Any) -> Booking | None:
    """The offer's booking while the poster is in checkout (AWAITING_PAYMENT), row-locked,
    or None. Call with the task locked and before locking the offer: task, booking, offer,
    the order a webhook's PAYMENT_FAILED (booking, then offers) also takes. Waiting behind
    a webhook that confirmed the payment returns None: the booking is no longer in
    checkout."""
    return (
        Booking.objects.select_for_update()
        .filter(offer_id=offer_id, status=S.AWAITING_PAYMENT)
        .first()
    )


# The reason a checkout ended by the offer's withdrawal records; bookings.checkout cancels
# its PaymentIntent with the offer's withdraw key.
WITHDRAWN_REASON = "offer_withdrawn"


def end_checkout_for_withdrawal(booking: Booking, now: dt.datetime) -> Booking:
    """The helper withdrew the accepted offer while the poster was paying (PLAN.md, "A
    helper can withdraw an offer"): the checkout fails, by the system, and the offers it
    declined reopen. Call in the withdrawal's transaction, after `lock_checkout` and after
    the offer is saved WITHDRAWN, so `_reopen_offers` leaves it withdrawn. The
    offer.withdrawn handler cancels the PaymentIntent; a payment Stripe confirms at the
    same moment finds the booking PAYMENT_FAILED and is refunded in full, and the poster
    told (bookings.checkout, key `booking:{id}:refund:withdrawn:v1`)."""
    return transition(
        booking.pk,
        S.PAYMENT_FAILED,
        actor_kind=A.SYSTEM,
        data={"reason": WITHDRAWN_REASON},
        now=now,
    )


def lock_active_booking(task: Task) -> Booking | None:
    """The task's active booking, row-locked for the transaction, or None. Call with the
    task already locked (tasks.services.update_task): task, then booking."""
    return active(Booking.objects.select_for_update().filter(task=task)).first()


def record_access_details_change(booking: Booking, poster: User) -> None:
    """Audit and announce new access details (doorbell, floor, gate code, key) on the task
    of `booking`, which `lock_active_booking` locked in this transaction (hard rule c).

    The user decided on 2026-11-03 that the poster may change access details while the
    booking is active, so a new gate code reaches the helper before the visit; every other
    detail of a booked task stays as booked. Neither row holds the details themselves: the
    helper's app reloads the task.
    """
    if not transaction.get_connection().in_atomic_block or not booking.is_active:
        raise RuntimeError("Access details change only inside an active booking's lock.")
    BookingEvent.objects.create(
        booking=booking,
        type="access_details_updated",
        actor_kind=A.POSTER,
        actor=poster,
        data={"task_id": str(booking.task_id)},
    )
    enqueue(
        ACCESS_DETAILS_TOPIC,
        aggregate=booking,
        payload={"booking_id": str(booking.pk), "task_id": str(booking.task_id)},
    )


def _slot_start(task: Task, slot_start: dt.datetime | None, now: dt.datetime) -> dt.datetime:
    if slot_start is not None and timezone.is_naive(slot_start):
        raise ApiError(ErrorCode.INVALID_TASK_DATES, "Give the day with its time zone.")
    if not task.flexible_date:
        if slot_start is not None or task.window_start is None:
            raise ApiError(ErrorCode.INVALID_TASK_DATES, "A fixed-date task keeps its date.")
        return task.window_start
    ends = task_ends_at(task)
    if slot_start is None or slot_start <= now or (ends is not None and slot_start > ends):
        raise ApiError(ErrorCode.INVALID_TASK_DATES, "Choose a day within the task's period.")
    return slot_start


def _lock_contact(contact_id: int | None) -> None:
    if contact_id is not None:
        list(CareContact.objects.select_for_update().filter(pk=contact_id))


def service_fee_cents(amount_cents: int, when: dt.datetime | None = None) -> int:
    """The service fee of the schedule in force at `when` (default: now) in whole cents,
    rounded half up: today 12%, at least €2.50 (hard rule b; PLAN.md, "Proposed fees and unit
    economics"): €30 → 360, €10 → 250."""
    return fee_schedule_at(when).fee_cents(amount_cents)


def transition(
    booking_id: Any,
    target: S,
    *,
    actor_kind: A,
    actor: User | None = None,
    data: Mapping[str, Any] | None = None,
    now: dt.datetime | None = None,
) -> Booking:
    """Move a booking to `target` for a poster, helper, staff user or the system.

    Raises BOOKING_NOT_FOUND when `actor` is not the booking's poster or helper (as
    `actor_kind` says), so IDs reveal nothing, and BOOKING_ACTION_NOT_ALLOWED when the
    move isn't in `TRANSITIONS` for that actor. Never confirms money: STRIPE_WEBHOOK
    moves go through `apply_stripe_transition`.
    """
    if actor_kind == A.STRIPE_WEBHOOK:
        raise ValueError("Stripe moves go through apply_stripe_transition().")
    try:
        pk = uuid.UUID(str(booking_id))
    except ValueError:
        raise ApiError(ErrorCode.BOOKING_NOT_FOUND, "No such booking.") from None
    try:
        with transaction.atomic():
            if target == S.PAYMENT_FAILED:
                # A failed checkout reopens offers, which depends on the poster's business
                # status: the poster's lock first, as everywhere (`lock_poster`).
                poster_id = Booking.objects.filter(pk=pk).values_list("poster_id", flat=True)
                lock_poster(poster_id.first())
            with locked_for_update(Booking.objects, pk) as booking:
                _check_actor(booking, target, actor_kind, actor)
                _apply(booking, target, actor_kind, actor, data, now or timezone.now())
    except Booking.DoesNotExist:
        raise ApiError(ErrorCode.BOOKING_NOT_FOUND, "No such booking.") from None
    return booking


def apply_stripe_transition(
    booking_id: Any,
    target: S,
    *,
    stripe_event_id: str,
    data: Mapping[str, Any] | None = None,
    now: dt.datetime | None = None,
) -> Booking | None:
    """Apply the move a verified Stripe webhook event confirms. Webhook handlers only.

    Must run inside the webhook transaction that recorded `stripe_event_id` and before
    it is marked processed (payments.webhooks). Returns None, changing nothing, when the
    booking is already in or past `target` (an out-of-order or replayed event). A move
    Stripe can't make from the current status also returns None and changes nothing, but
    records a `SUPPORT_FLAG` BookingEvent and alerts support through Sentry (IDs only).
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("apply_stripe_transition() must run in the webhook transaction.")
    if not StripeEvent.objects.filter(pk=stripe_event_id, processed_at__isnull=True).exists():
        raise PermissionError("Only a recorded, unprocessed Stripe event confirms money.")
    if target == S.PAYMENT_FAILED:
        lock_poster(
            Booking.objects.filter(pk=booking_id).values_list("poster_id", flat=True).first()
        )
    with locked_for_update(Booking.objects, booking_id) as booking:
        current = S(booking.status)
        if not is_allowed(current, target, A.STRIPE_WEBHOOK):
            if not _is_stale(current, target):
                flag_for_support(
                    booking,
                    "stripe_transition_refused",
                    {"from": current.value, "to": target.value, "stripe_event": stripe_event_id},
                )
            return None
        _apply(
            booking,
            target,
            A.STRIPE_WEBHOOK,
            None,
            {**(data or {}), "stripe_event": stripe_event_id},
            now or timezone.now(),
        )
    return booking


def flag_for_support(booking: Booking, reason: str, data: Mapping[str, Any]) -> None:
    """Something about `booking`'s money needs a person: a `SUPPORT_FLAG` BookingEvent,
    for the staff console, and a money alert (bookings/alerts.py). `reason` is an enum key;
    `data` holds IDs and enum keys only. Its `stripe_event` and `payout` reach Sentry."""
    BookingEvent.objects.create(
        booking=booking,
        type=SUPPORT_FLAG,
        actor_kind=A.SYSTEM,
        data={"reason": reason, **data},
    )
    event_id = str(data.get("stripe_event") or "")
    event_type = (
        StripeEvent.objects.filter(pk=event_id).values_list("type", flat=True).first() or ""
    )
    money_alert(
        "bookings: a booking was flagged for support",
        booking_id=booking.pk,
        reason=reason,
        stripe_event_type=event_type,
        stripe_event_id=event_id,
        stripe_object_id=str(data.get("payout") or data.get("payment_intent") or ""),
    )


def _is_stale(current: S, target: S) -> bool:
    if current == target:
        return True
    if current == S.PAYMENT_FAILED or target == S.DISPUTED:
        return False
    return _RANK[current] > _RANK[target]


def _check_actor(booking: Booking, target: S, actor_kind: A, actor: User | None) -> None:
    if actor_kind == A.POSTER and (actor is None or actor.pk != booking.poster_id):
        raise Booking.DoesNotExist
    if actor_kind == A.HELPER and (actor is None or actor.pk != booking.helper_id):
        raise Booking.DoesNotExist
    if actor_kind in (A.POSTER, A.HELPER) and actor is not None and not actor.is_active:
        # A suspended or deactivated account; support acts for it (ADMIN).
        raise ApiError(ErrorCode.UNAUTHENTICATED, "Sign in first.")
    if actor_kind == A.ADMIN and (actor is None or not actor.is_staff or not actor.is_active):
        raise ApiError(ErrorCode.BOOKING_ACTION_NOT_ALLOWED, "Staff only.")
    if actor_kind == A.SYSTEM and actor is not None:
        raise ApiError(ErrorCode.BOOKING_ACTION_NOT_ALLOWED, "The system acts without a user.")
    if not is_allowed(booking.status, target, actor_kind):
        raise ApiError(
            ErrorCode.BOOKING_ACTION_NOT_ALLOWED,
            f"{actor_kind} can't move a booking from {booking.status} to {target}.",
        )
    withdrawing = booking.status == S.CANCEL_REQUESTED and target == S.FUNDS_HELD
    if withdrawing and actor_kind != A.ADMIN and booking.cancel_requested_by != actor_kind:
        raise ApiError(
            ErrorCode.BOOKING_ACTION_NOT_ALLOWED, "Only who asked to cancel can withdraw it."
        )
    # A poster's cancel whose settlement is open, a withdrawal statement, or a reported
    # no-show is final for the two sides (bookings.cancel, bookings.arrival): money is
    # moving, or support works out the split or decides the contest.
    if (
        booking.status == S.CANCEL_REQUESTED
        and target in (S.FUNDS_HELD, S.DISPUTED)
        and actor_kind in (A.POSTER, A.HELPER)
        and (
            booking.cancel_reason in (WITHDRAWAL_CANCEL_REASON, NO_SHOW_CANCEL_REASON)
            or CancellationSettlement.objects.filter(booking=booking).exists()
        )
    ):
        raise ApiError(
            ErrorCode.BOOKING_ACTION_NOT_ALLOWED, "This cancellation is already being settled."
        )
    # "Other side disagrees" (plan): the requester withdraws instead of disputing.
    disputing = booking.status == S.CANCEL_REQUESTED and target == S.DISPUTED
    if disputing and booking.cancel_requested_by == actor_kind:
        raise ApiError(
            ErrorCode.BOOKING_ACTION_NOT_ALLOWED, "The other side disputes a cancel request."
        )


def _apply(
    booking: Booking,
    target: S,
    actor_kind: A,
    actor: User | None,
    data: Mapping[str, Any] | None,
    now: dt.datetime,
) -> None:
    source = S(booking.status)
    booking.status = target
    booking.status_changed_at = now
    fields = ["status", "status_changed_at", "updated_at"]
    if target == S.PAYMENT_REQUESTED:
        booking.payment_requested_at = now
        fields.append("payment_requested_at")
    elif target == S.RELEASED:
        booking.released_at = now
        booking.released_by = actor_kind
        fields += ["released_at", "released_by"]
    elif target == S.CANCEL_REQUESTED:
        booking.cancel_requested_at = now
        booking.cancel_requested_by = actor_kind
        fields += ["cancel_requested_at", "cancel_requested_by"]
    elif source == S.CANCEL_REQUESTED and target == S.FUNDS_HELD:
        booking.cancel_requested_at = None
        booking.cancel_requested_by = ""
        fields += ["cancel_requested_at", "cancel_requested_by"]
    if target in Booking.TERMINAL:
        booking.closed_at = now
        fields.append("closed_at")
    booking.save(update_fields=fields)
    if source == S.CANCEL_REQUESTED and target != S.CANCEL_REQUESTED:
        # Whatever moved the booking on (a chargeback, a settlement, support), a no-fault
        # request still open is moot: close it, or it would wait for support forever and
        # keep the helper's access to the place (models.Booking.is_active).
        NoFaultRequest.objects.filter(booking=booking, closed_at__isnull=True).update(closed_at=now)
    if target == S.PAYMENT_FAILED:
        _reopen_offers(booking, now)
        credit.restore(booking, now, reason="checkout_failed")
    elif target == S.REFUNDED:
        # Any full refund (a settlement, or a dispute decided for the poster) gives the
        # credit back; confirm_refund also restores it for a partly settled cancel.
        credit.restore(booking, now, reason="refunded")
    _legal_hold(booking, source, target, now)
    _record(booking, source, target, actor_kind, actor, data or {})


def _legal_hold(booking: Booking, source: S, target: S, now: dt.datetime) -> None:
    """An open dispute or chargeback keeps the task's photos and the staff notes on the
    booking as evidence (BEAV-63, staff.retention). Booking evidence photos join them
    with BEAV-116."""
    if target == S.DISPUTED:
        place_legal_hold(booking.task.photos.all())
        staff_retention.place_legal_hold(Booking._meta.label_lower, str(booking.pk))
    elif source == S.DISPUTED:
        lift_legal_hold(booking.task.photos.all(), resolved_at=now)
        staff_retention.lift_legal_hold(Booking._meta.label_lower, str(booking.pk), resolved_at=now)


def _record(
    booking: Booking,
    source: S | None,
    target: S,
    actor_kind: A,
    actor: User | None,
    data: Mapping[str, Any],
) -> None:
    BookingEvent.objects.create(
        booking=booking,
        type=target.lower(),
        actor_kind=actor_kind,
        actor=actor,
        data={"from": source.value if source else None, "to": target.value, **data},
    )
    enqueue(
        UPDATED_TOPIC,
        aggregate=booking,
        payload={"booking_id": str(booking.pk), "status": target.value},
    )
