"""No-fault cancellation (hard rules a, c and g; PLAN.md, "TaskBeavo cancellation policy":
the no-fault row and the 48-hour response; BEAV-123).

Either side cites a no-fault reason (weather, hospitalisation or death of the person
helped, a declared emergency, or both agree) for a booking whose money is held, before
or after the start (`request_no_fault`). The booking moves to CANCEL_REQUESTED and a
`NoFaultRequest` waits 48 hours for the other side (`respond_to_no_fault`):

- agrees: the request stands, a full refund (`cancellation_outcome`'s NO_FAULT);
- disagrees: support decides (`decide_no_fault`, the "Cancellations & withdrawals" queue);
- doesn't reply (`settle_unanswered`, Beat, user decision 2026-11-07): when the poster
  asked, the request stands and the poster gets the full refund (only the consumer gains
  from the silence); when the helper asked, support decides, and nothing changes until
  then: no refund, no reliability fault, no credit. The helper's answer to a poster's
  request once the 48 hours are up is refused (NO_FAULT_REPLY_CLOSED), even before the
  sweep ran: the deadline decides, so a late "disagree" can't hold up the refund. The
  poster may answer a helper's request until support decided: a late agree is the full
  refund, a late disagree is recorded for support (coordinator decision 2026-11-07).

Support upholds the reason (a full refund) or rejects it. A rejection applies the
ordinary policy to whoever asked, as of when they asked: a helper's request becomes a
helper cancel (full refund, the EUR 5 credit if it was late, a reliability fault); a
poster's becomes the poster's cancel at that moment (`cancel.preview`'s row), and if no
cancel was possible then (WORK_STARTED) the booking goes back to FUNDS_HELD and the
poster may open a dispute. NEEDS REVIEW (lawyer, BEAV-39): the rejection rule.

Once the helper requested payment, no request can be made (NotACancellation). The amounts
all come from bookings/cancellation.py; the refund runs through bookings/settlement.py
and only Stripe's webhook confirms it.
"""

import datetime as dt
from typing import Any

from django.db import transaction
from django.utils import timezone

from accounts.models import User
from bookings import cancellation, settlement, state_machine
from bookings.cancel import _pk, helper_is_trader
from bookings.cancellation import NoFaultReason, NotACancellation, Outcome, Party
from bookings.helper_cancel import settle_helper_fault
from bookings.models import Booking, BookingEvent, HelperFault, NoFaultRequest
from core.errors import ApiError, ErrorCode
from core.models import ActorKind
from core.outbox import enqueue
from staff.roles import Capability, can

S = Booking.Status
R = NoFaultRequest.Response

RESPONSE_WINDOW = dt.timedelta(hours=48)
# `Booking.cancel_reason` while a no-fault request is open or once it settled.
CANCEL_REASON = "NO_FAULT"
# The other side is asked to respond: a live event and the cancellation_requested push.
REQUESTED_TOPIC = "booking.no_fault_requested"
# Support has to decide: the requester is told it is under review.
SUPPORT_TOPIC = "booking.no_fault_for_support"
# Support decided: both sides are told the decision and what it means.
DECIDED_TOPIC = "booking.no_fault_decided"
# The request stood without support: the other side agreed, or the poster's went
# unanswered for 48 hours. Both sides get the `booking.no_fault_cancelled` push and email.
STOOD_TOPIC = "booking.no_fault_stood"
REQUESTED = "no_fault_requested"
RESPONDED = "no_fault_responded"
DECIDED = "no_fault_decided"

_PARTY = {ActorKind.POSTER: Party.POSTER, ActorKind.HELPER: Party.HELPER}


def _now(now: dt.datetime | None) -> dt.datetime:
    return now or timezone.now()


def open_request(booking: Booking) -> NoFaultRequest | None:
    """`booking`'s open no-fault request, if any."""
    return NoFaultRequest.objects.filter(booking=booking, closed_at__isnull=True).first()


def latest_request(booking: Booking) -> NoFaultRequest | None:
    """The open request, or the last one."""
    return NoFaultRequest.objects.filter(booking=booking).order_by("-requested_at").first()


def _side(booking: Booking, user: User) -> ActorKind:
    if booking.poster_id == user.pk:
        return ActorKind.POSTER
    if booking.helper_id == user.pk:
        return ActorKind.HELPER
    raise ApiError(ErrorCode.BOOKING_NOT_FOUND, "No such booking.")


def _lock(booking_id: Any) -> Booking:
    try:
        return Booking.objects.select_for_update().get(pk=_pk(booking_id))
    except Booking.DoesNotExist:
        raise ApiError(ErrorCode.BOOKING_NOT_FOUND, "No such booking.") from None


def reply_closed(request: NoFaultRequest, now: dt.datetime) -> bool:
    """Whether the helper's 48 hours to answer the poster's `request` are up. A poster's
    answer has no deadline of its own: it closes when support decides (coordinator
    decision 2026-11-07)."""
    return request.requested_by == ActorKind.POSTER and now >= request.respond_by


def can_request(booking: Booking, side: ActorKind | str | None = None) -> bool:
    """Whether `side` (either side when None) may cite a no-fault reason now: the money is
    held, and that side hasn't asked on this booking before. One request per side, so a
    reason support rejected can't be cited again in a loop."""
    if booking.status != S.FUNDS_HELD:
        return False
    if side is None:
        return True
    return not NoFaultRequest.objects.filter(booking=booking, requested_by=side).exists()


def request_no_fault(
    user: User, booking_id: Any, reason: NoFaultReason, *, now: dt.datetime | None = None
) -> Booking:
    """The poster or helper cites a no-fault reason (module docstring). Returns the
    booking, CANCEL_REQUESTED. Errors: BOOKING_NOT_FOUND, BOOKING_ACTION_NOT_ALLOWED (not
    held, or payment already requested)."""
    if reason == NoFaultReason.HELPER_LATE:
        # Only the poster's free cancel after "Running late" gives it (`cancel_for_late_helper`).
        raise ApiError(ErrorCode.BOOKING_ACTION_NOT_ALLOWED, "This reason can't be cited here.")
    now = _now(now)
    with transaction.atomic():
        booking = _lock(booking_id)
        side = _side(booking, user)
        # Once payment was requested (PAYMENT_REQUESTED) it isn't a cancellation either:
        # the poster releases or opens a dispute.
        if not can_request(booking, side):
            raise ApiError(
                ErrorCode.BOOKING_ACTION_NOT_ALLOWED, "This booking can't be cancelled now."
            )
        booking = state_machine.transition(
            booking.pk,
            S.CANCEL_REQUESTED,
            actor_kind=side,
            actor=user,
            # The generic label only: the specific reason may be health data (GDPR Art. 9)
            # and stays on the request, for support (cancellation.NoFaultLabel).
            data={"reason": CANCEL_REASON, "no_fault_label": cancellation.no_fault_label(reason)},
            now=now,
        )
        booking.cancel_reason = CANCEL_REASON
        booking.save(update_fields=["cancel_reason", "updated_at"])
        request = NoFaultRequest.objects.create(
            booking=booking,
            reason=reason.value,
            requested_by=side,
            requested_at=now,
            respond_by=now + RESPONSE_WINDOW,
        )
        BookingEvent.objects.create(
            booking=booking,
            type=REQUESTED,
            actor_kind=side,
            actor=user,
            created_at=now,
            data={"request_id": request.pk, "label": cancellation.no_fault_label(reason)},
        )
        enqueue(REQUESTED_TOPIC, aggregate=booking, payload=_payload(booking, request))
    return booking


def respond_to_no_fault(
    user: User, booking_id: Any, *, agree: bool, now: dt.datetime | None = None
) -> Booking:
    """The other side agrees (a full refund) or disagrees (support decides). The helper
    answers a poster's request within its 48 hours; the poster answers a helper's request
    until support decided, even once the sweep sent it to support (coordinator decision
    2026-11-07): a late agree is the full refund, a late disagree is recorded for support.
    Errors: BOOKING_NOT_FOUND, BOOKING_ACTION_NOT_ALLOWED (no request waiting for this
    user's answer, or it already has one), NO_FAULT_REPLY_CLOSED (the time to answer is
    over: the helper's 48 hours, or support decided, or the request already settled)."""
    now = _now(now)
    with transaction.atomic():
        booking = _lock(booking_id)
        side = _side(booking, user)
        request = open_request(booking)
        if request is None:
            last = latest_request(booking)
            if last is not None and last.requested_by != side and _unanswered(last):
                raise ApiError(
                    ErrorCode.NO_FAULT_REPLY_CLOSED, "The time to answer this request is over."
                )
        if (
            request is None
            or request.requested_by == side
            or not _unanswered(request)
            or booking.status != S.CANCEL_REQUESTED
        ):
            raise ApiError(
                ErrorCode.BOOKING_ACTION_NOT_ALLOWED, "There is no cancellation request to answer."
            )
        # The deadline decides, not whether the sweep has run yet: the helper's late answer
        # is no answer, so the poster's request stands (`settle_unanswered`).
        if reply_closed(request, now):
            raise ApiError(
                ErrorCode.NO_FAULT_REPLY_CLOSED, "The time to answer this request is over."
            )
        at_support = request.support_needed_at is not None
        request.response = R.AGREED if agree else R.DISAGREED
        request.responded_at = now
        request.save(update_fields=["response", "responded_at"])
        BookingEvent.objects.create(
            booking=booking,
            type=RESPONDED,
            actor_kind=side,
            actor=user,
            created_at=now,
            data={"request_id": request.pk, "response": request.response},
        )
        if agree:
            _uphold(booking, request, now)
        elif not at_support:
            _to_support(booking, request, now)
        # A late disagree with the request already at support: recorded for support, who
        # still decide.
    return booking


def cancel_for_late_helper(booking: Booking, poster: User, now: dt.datetime) -> NoFaultRequest:
    """The poster cancels for free after the helper reported running late (user decision
    2026-11-08; bookings/arrival.py): a no-fault cancel the helper's own report already
    agreed to. A full refund at once, with no 48-hour wait, no EUR 5 credit and no
    reliability fault. `booking` is locked and FUNDS_HELD in the caller's transaction."""
    reason = NoFaultReason.HELPER_LATE
    booking = state_machine.transition(
        booking.pk,
        S.CANCEL_REQUESTED,
        actor_kind=ActorKind.POSTER,
        actor=poster,
        data={"reason": CANCEL_REASON, "no_fault_label": cancellation.no_fault_label(reason)},
        now=now,
    )
    booking.cancel_reason = CANCEL_REASON
    booking.save(update_fields=["cancel_reason", "updated_at"])
    request = NoFaultRequest.objects.create(
        booking=booking,
        reason=reason.value,
        requested_by=ActorKind.POSTER,
        requested_at=now,
        respond_by=now,
        response=R.AGREED,
        responded_at=now,
    )
    BookingEvent.objects.create(
        booking=booking,
        type=REQUESTED,
        actor_kind=ActorKind.POSTER,
        actor=poster,
        created_at=now,
        data={"request_id": request.pk, "label": cancellation.no_fault_label(reason)},
    )
    BookingEvent.objects.create(
        booking=booking,
        type=RESPONDED,
        actor_kind=ActorKind.HELPER,
        created_at=now,
        data={"request_id": request.pk, "response": R.AGREED, "by": "late_report"},
    )
    _uphold(booking, request, now)
    return request


def _unanswered(request: NoFaultRequest) -> bool:
    """No answer from the other side yet: none, or the sweep's NO_REPLY."""
    return request.response in ("", R.NO_REPLY)


def settle_unanswered(now: dt.datetime | None = None) -> int:
    """Beat task body: settle each request whose 48 hours passed without an answer (module
    docstring). Returns how many it handled."""
    now = _now(now)
    due = list(
        NoFaultRequest.objects.filter(closed_at__isnull=True, response="", respond_by__lte=now)
        .order_by("respond_by")
        .values_list("booking_id", flat=True)[:200]
    )
    handled = 0
    for booking_id in due:
        with transaction.atomic():
            booking = _lock(booking_id)
            request = open_request(booking)
            if request is None or request.response or request.respond_by > now:
                continue
            if booking.status != S.CANCEL_REQUESTED:
                # A chargeback or support moved the booking on: the request is moot, and
                # closing it keeps it from crowding out the due ones.
                _close(request, now)
                continue
            request.response = R.NO_REPLY
            request.responded_at = now
            request.save(update_fields=["response", "responded_at"])
            BookingEvent.objects.create(
                booking=booking,
                type=RESPONDED,
                actor_kind=ActorKind.SYSTEM,
                created_at=now,
                data={"request_id": request.pk, "response": R.NO_REPLY},
            )
            if request.requested_by == ActorKind.POSTER:
                _uphold(booking, request, now)
            else:
                _to_support(booking, request, now)
            handled += 1
    return handled


def awaiting_support(booking: Booking) -> bool:
    request = open_request(booking)
    return request is not None and request.support_needed_at is not None


def decide_no_fault(
    staff: User, booking_id: Any, *, uphold: bool, now: dt.datetime | None = None
) -> Booking:
    """Support decides a request that waits for them (module docstring). Support and
    finance only (the money roles, `Capability.STRIPE_IDS`)."""
    if not can(staff, Capability.STRIPE_IDS):
        raise ApiError(ErrorCode.BOOKING_ACTION_NOT_ALLOWED, "Support or finance only.")
    now = _now(now)
    with transaction.atomic():
        booking = _lock(booking_id)
        request = open_request(booking)
        if (
            request is None
            or request.support_needed_at is None
            or booking.status != S.CANCEL_REQUESTED
        ):
            raise ApiError(ErrorCode.BOOKING_ACTION_NOT_ALLOWED, "No request waits for support.")
        request.decision = (
            NoFaultRequest.Decision.UPHELD if uphold else NoFaultRequest.Decision.REJECTED
        )
        request.decided_at = now
        request.decided_by = staff
        request.save(update_fields=["decision", "decided_at", "decided_by"])
        BookingEvent.objects.create(
            booking=booking,
            type=DECIDED,
            actor_kind=ActorKind.ADMIN,
            actor=staff,
            created_at=now,
            data={"request_id": request.pk, "decision": request.decision},
        )
        if uphold:
            _uphold(booking, request, now)
        else:
            _reject(booking, request, staff, now)
        enqueue(
            DECIDED_TOPIC,
            aggregate=booking,
            payload={**_payload(booking, request), "decision": request.decision},
        )
    return booking


def _outcome(
    booking: Booking,
    request: NoFaultRequest,
    *,
    no_fault: bool,
    now: dt.datetime,
) -> Outcome:
    return cancellation.cancellation_outcome(
        cancelled_by=_PARTY[ActorKind(request.requested_by)],
        no_fault_reason=NoFaultReason(request.reason) if no_fault else None,
        amount_cents=booking.amount_cents,
        fee_cents=booking.fee_cents,
        fee_percent=booking.fee_percent,
        accepted_at=booking.created_at,
        slot_start=booking.slot_start,
        now=now,
        helper_is_trader=helper_is_trader(booking),
    )


def _uphold(booking: Booking, request: NoFaultRequest, now: dt.datetime) -> None:
    """The request stands: a full refund, nobody's fault. Unless support decided it (both
    sides hear that from DECIDED_TOPIC), both sides are told it's cancelled."""
    settlement.open_settlement(booking, _outcome(booking, request, no_fault=True, now=now))
    _close(request, now)
    if not request.decision:
        enqueue(STOOD_TOPIC, aggregate=booking, payload=_payload(booking, request))


def _reject(booking: Booking, request: NoFaultRequest, staff: User, now: dt.datetime) -> None:
    """The ordinary policy for whoever asked, as of when they asked."""
    _close(request, now)
    try:
        outcome = _outcome(booking, request, no_fault=False, now=request.requested_at)
    except NotACancellation:
        # A poster's request at or after the start: no cancel to make. The booking is
        # held again and the poster may open a dispute.
        booking.cancel_reason = ""
        booking.save(update_fields=["cancel_reason", "updated_at"])
        state_machine.transition(
            booking.pk,
            S.FUNDS_HELD,
            actor_kind=ActorKind.ADMIN,
            actor=staff,
            data={"reason": "no_fault_rejected"},
            now=now,
        )
        return
    if request.requested_by == ActorKind.HELPER:
        settle_helper_fault(booking, outcome, HelperFault.Kind.NO_FAULT_REJECTED, now, decided=True)
    else:
        settlement.open_settlement(booking, outcome)


def _to_support(booking: Booking, request: NoFaultRequest, now: dt.datetime) -> None:
    request.support_needed_at = now
    request.save(update_fields=["support_needed_at"])
    enqueue(SUPPORT_TOPIC, aggregate=booking, payload=_payload(booking, request))


def _close(request: NoFaultRequest, now: dt.datetime) -> None:
    request.closed_at = now
    request.save(update_fields=["closed_at"])


def _payload(booking: Booking, request: NoFaultRequest) -> dict[str, Any]:
    return {
        "booking_id": str(booking.pk),
        "task_id": str(booking.task_id),
        "request_id": request.pk,
        "requested_by": request.requested_by,
    }
