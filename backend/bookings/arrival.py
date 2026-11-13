"""The day of the job: "On my way", arrival and the no-show timeline (PLAN.md, "Helper
no-show and late arrival"; hard rules c, g and k; BEAV-124).

The helper taps "On my way" (an optional ETA), "Running late" (a new ETA) and "I've
arrived". Each tap is a timestamp on `BookingArrival` and a `BookingEvent`, as evidence
for the no-show timeline and for support in a dispute. On their own they never change who
pays what (user decision 2026-11-07), and they are never a location: nothing is tracked
during the task (hard rule k).

The timeline (`run_timeline`, Celery Beat every minute), for a booking whose money is held:

- reminders to the helper 2 hours and 30 minutes before the booked start;
- 15 minutes after the start with neither "On my way", "Running late" nor "I've arrived":
  "Are you on your way?" to the helper, who answers "Running late" with a new ETA, "I've
  arrived", or "I can't come" (cancelAsHelper with CANT_COME: the helper cancellation);
- `no_show_due`: 60 minutes after the start with no answer, or 45 minutes after the
  start or the helper's ETA, whichever is later, once they said they were on their way or
  late: "Did your helper come?" to the poster (user decision 2026-11-08). After "I've
  arrived" the poster isn't asked, but may still say no 45 minutes after the start or the
  tap, whichever is later: the tap is evidence for the contest, never the end of the route.

The poster answers "Yes" (`confirm_helper_came`), or "No" once `no_show_due` passed. "No"
moves the booking to CANCEL_REQUESTED (the helper loses the address at once) and gives the
helper 24 hours to answer "I was there" (`contest_no_show`, user decision 2026-11-08), so a
forgotten tap or a wrong answer doesn't cost a helper who did the work. A contest goes to
support (`decide_no_show`): upheld, or uncontested after 24 h (`settle_uncontested_no_shows`,
Beat), it is the helper cancellation from BEAV-123 (HELPER_CANCEL as of the poster's no: a
full refund, the EUR 5 credit under the late window) with a NO_SHOW `HelperFault`;
rejected, the booking is held again. That fault only queues a person's review at two in 30
days; nothing here restricts, ranks or deactivates anyone (hard rule k).

After "Running late" the poster keeps the booking or cancels for free (`respond_to_late`)
until `late_answer_until`: a no-fault cancel the helper's report already agreed to, a full
refund without the credit or a fault (`no_fault.cancel_for_late_helper`, user decision
2026-11-08).

Early start (user decision 2026-11-07): the booked start is the only cancellation cutoff.
An arrived helper may ask to start before it ("Start now"); only the poster's confirmation
moves `Booking.slot_start` to that moment, so the after-start rules apply from then (no
cancel, WORK_STARTED; the dispute route instead). The request lapses at the booked start.

Every change locks the booking (select_for_update) and writes a `BookingEvent` and an
outbox row in one transaction (hard rule c). The outbox row (`ARRIVAL_TOPIC`, IDs and a
step key only) sends the live event to both sides and the step's push, with the email
fallback, in the recipient's language (bookings/outbox_handlers.py). Amounts come only
from bookings/cancellation.py.
"""

import datetime as dt
import logging
from enum import StrEnum
from typing import Any

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import User
from bookings import cancel, cancellation, helper_cancel, no_fault, state_machine
from bookings.alerts import money_alert
from bookings.cancel import _pk, helper_is_trader
from bookings.cancellation import NotACancellation, Outcome, Party
from bookings.models import Booking, BookingArrival, BookingEvent, HelperFault, NoFaultRequest
from core.errors import ApiError, ErrorCode
from core.models import ActorKind
from core.outbox import enqueue
from staff.roles import Capability, can

logger = logging.getLogger(__name__)

S = Booking.Status

# The taps open this long before the booked start, so a tap days early is a mistake.
TAPS_OPEN_BEFORE = dt.timedelta(hours=6)
REMINDER_EARLY = dt.timedelta(hours=2)
REMINDER_LATE = dt.timedelta(minutes=30)
# A reminder isn't sent once most of its lead time has gone (booked late, or a Beat gap).
REMINDER_EARLY_FLOOR = dt.timedelta(minutes=90)
REMINDER_LATE_FLOOR = dt.timedelta(minutes=15)
PROMPT_AFTER_START = dt.timedelta(minutes=15)
# "No reply in 45 minutes" after the prompt: the poster is asked at the start + 60 minutes.
NO_REPLY_WINDOW = dt.timedelta(minutes=45)
ETA_MAX_MINUTES = 240
# The poster always has at least this long to answer a late report, however short the ETA.
LATE_ANSWER_MIN = dt.timedelta(minutes=30)
# However often the helper taps, the poster may say they didn't come by this long after the
# start: the longest ETA plus the 45 minutes (security review of BEAV-124).
NO_SHOW_DUE_LATEST = dt.timedelta(minutes=ETA_MAX_MINUTES) + NO_REPLY_WINDOW

# The live event to both sides and the step's push (bookings/outbox_handlers.py).
ARRIVAL_TOPIC = "booking.arrival"
# `Booking.cancel_reason` of a no-show the poster reported.
NO_SHOW_REASON = state_machine.NO_SHOW_CANCEL_REASON
# How long the helper has to answer "I was there" (user decision 2026-11-08).
NO_SHOW_CONTEST_WINDOW = dt.timedelta(hours=24)
# The support flag of a contested no-show (state_machine.flag_for_support).
NO_SHOW_CONTESTED_FLAG = "no_show_contested"
# The support flag (and Sentry money alert) of an uncontested no-show the sweep failed to
# settle `SETTLE_ALERT_AFTER` times. The sweep keeps retrying it with backoff
# (`SETTLE_RETRY_BASE` doubling up to `SETTLE_RETRY_MAX`), and support can settle it by hand
# (`decide_no_show`). Review R1-1.
NO_SHOW_SETTLE_FAILED_FLAG = "no_show_settle_failed"
SETTLE_ALERT_AFTER = 3
SETTLE_RETRY_BASE = dt.timedelta(minutes=15)
SETTLE_RETRY_MAX = dt.timedelta(hours=6)


class Step(StrEnum):
    """What happened, in the outbox payload and `BookingEvent.type` (lower case)."""

    ON_MY_WAY = "ON_MY_WAY"
    RUNNING_LATE = "RUNNING_LATE"
    ARRIVED = "ARRIVED"
    LATE_ACCEPTED = "LATE_ACCEPTED"
    CAME_CONFIRMED = "CAME_CONFIRMED"
    # The poster said no; the helper has 24 h to contest.
    NO_SHOW_REPORTED = "NO_SHOW_REPORTED"
    NO_SHOW_CONTESTED = "NO_SHOW_CONTESTED"
    # Settled as the helper cancellation: uncontested, or support upheld it.
    NO_SHOW_CONFIRMED = "NO_SHOW_CONFIRMED"
    # Support upheld a contested no-show: both sides are told.
    NO_SHOW_UPHELD = "NO_SHOW_UPHELD"
    # Support found the helper was there: the booking is held again.
    NO_SHOW_REJECTED = "NO_SHOW_REJECTED"
    REMINDER_2H = "REMINDER_2H"
    REMINDER_30M = "REMINDER_30M"
    ON_THE_WAY_PROMPT = "ON_THE_WAY_PROMPT"
    ASK_POSTER = "ASK_POSTER"
    EARLY_START_REQUESTED = "EARLY_START_REQUESTED"
    EARLY_START_CONFIRMED = "EARLY_START_CONFIRMED"
    EARLY_START_DECLINED = "EARLY_START_DECLINED"


def _now(now: dt.datetime | None) -> dt.datetime:
    return now or timezone.now()


def _not_allowed(message: str) -> ApiError:
    return ApiError(ErrorCode.BOOKING_ACTION_NOT_ALLOWED, message)


def _lock(user: User, booking_id: Any, side: ActorKind) -> tuple[Booking, BookingArrival]:
    """`booking_id` locked for its poster or helper (`side`), with its arrival record. Any
    other user gets BOOKING_NOT_FOUND, so IDs reveal nothing."""
    try:
        booking = Booking.objects.select_for_update().get(pk=_pk(booking_id))
    except Booking.DoesNotExist:
        raise ApiError(ErrorCode.BOOKING_NOT_FOUND, "No such booking.") from None
    owner = booking.poster_id if side == ActorKind.POSTER else booking.helper_id
    if owner != user.pk:
        raise ApiError(ErrorCode.BOOKING_NOT_FOUND, "No such booking.")
    if not user.is_active:
        raise ApiError(ErrorCode.UNAUTHENTICATED, "Sign in first.")
    return booking, _arrival(booking)


def _arrival(booking: Booking) -> BookingArrival:
    arrival, _ = BookingArrival.objects.get_or_create(booking=booking)
    return arrival


def _held(booking: Booking) -> bool:
    """The money is held for a job that can still happen."""
    return booking.status == S.FUNDS_HELD and booking.task_reopened_at is None


def _here(arrival: BookingArrival) -> bool:
    """The helper is there: they tapped "I've arrived", or the poster said they came."""
    return arrival.arrived_at is not None or arrival.poster_confirmed_came_at is not None


def _eta(now: dt.datetime, minutes: int) -> dt.datetime:
    if not 1 <= minutes <= ETA_MAX_MINUTES:
        raise ApiError(ErrorCode.INVALID_ETA, "The ETA is out of range.")
    return now + dt.timedelta(minutes=minutes)


def _record(
    booking: Booking,
    arrival: BookingArrival,
    step: Step,
    fields: list[str],
    *,
    actor_kind: ActorKind,
    actor: User | None,
    now: dt.datetime,
    data: dict[str, Any] | None = None,
) -> None:
    arrival.save(update_fields=[*fields, "updated_at"])
    BookingEvent.objects.create(
        booking=booking,
        type=step.lower(),
        actor_kind=actor_kind,
        actor=actor,
        created_at=now,
        data={"step": step.value, **(data or {})},
    )
    enqueue(
        ARRIVAL_TOPIC,
        aggregate=booking,
        payload={"booking_id": str(booking.pk), "task_id": str(booking.task_id), "step": step},
    )


def _taps_open(booking: Booking, now: dt.datetime) -> None:
    if not _held(booking):
        raise _not_allowed("This booking isn't waiting for its job.")
    if now < booking.slot_start - TAPS_OPEN_BEFORE:
        raise _not_allowed("This opens on the day of the job.")


# --- The helper's taps. ---


def on_my_way(
    helper: User, booking_id: Any, eta_minutes: int | None, *, now: dt.datetime | None = None
) -> Booking:
    """ "On my way", with an optional ETA in minutes from now."""
    now = _now(now)
    with transaction.atomic():
        booking, arrival = _lock(helper, booking_id, ActorKind.HELPER)
        _taps_open(booking, now)
        if _here(arrival):
            raise _not_allowed("You have already arrived.")
        if arrival.on_my_way_at is not None or arrival.late_reported_at is not None:
            # A later ETA goes through Running late, which asks the poster again.
            raise _not_allowed("You already said you're on your way.")
        arrival.on_my_way_at = now
        arrival.eta_at = None if eta_minutes is None else _eta(now, eta_minutes)
        _record(
            booking,
            arrival,
            Step.ON_MY_WAY,
            ["on_my_way_at", "eta_at"],
            actor_kind=ActorKind.HELPER,
            actor=helper,
            now=now,
            data={"eta_minutes": eta_minutes},
        )
    return booking


def running_late(
    helper: User, booking_id: Any, eta_minutes: int, *, now: dt.datetime | None = None
) -> Booking:
    """ "Running late": a new ETA past the booked start. The poster may keep the booking or
    cancel for free (`respond_to_late`); a later report asks the poster again."""
    now = _now(now)
    with transaction.atomic():
        booking, arrival = _lock(helper, booking_id, ActorKind.HELPER)
        _taps_open(booking, now)
        if _here(arrival):
            raise _not_allowed("You have already arrived.")
        if _support_decided(booking, arrival):
            raise _not_allowed("Support has decided on this booking.")
        eta = _eta(now, eta_minutes)
        if eta <= booking.slot_start:
            raise ApiError(ErrorCode.INVALID_ETA, "That is before the booked start.")
        arrival.late_reported_at = now
        arrival.eta_at = eta
        # Only ever later: a second report can't shorten the poster's time to answer.
        arrival.late_answer_until = max(
            eta, now + LATE_ANSWER_MIN, arrival.late_answer_until or eta
        )
        arrival.late_accepted_at = None
        _record(
            booking,
            arrival,
            Step.RUNNING_LATE,
            ["late_reported_at", "eta_at", "late_answer_until", "late_accepted_at"],
            actor_kind=ActorKind.HELPER,
            actor=helper,
            now=now,
            data={"eta_minutes": eta_minutes},
        )
    return booking


def arrived(helper: User, booking_id: Any, *, now: dt.datetime | None = None) -> Booking:
    """ "I've arrived". Once only; it ends the no-show timeline."""
    now = _now(now)
    with transaction.atomic():
        booking, arrival = _lock(helper, booking_id, ActorKind.HELPER)
        _taps_open(booking, now)
        if arrival.arrived_at is not None:
            raise _not_allowed("You have already arrived.")
        arrival.arrived_at = now
        _record(
            booking,
            arrival,
            Step.ARRIVED,
            ["arrived_at"],
            actor_kind=ActorKind.HELPER,
            actor=helper,
            now=now,
        )
    return booking


# --- The poster's answers. ---


def _support_decided(booking: Booking, arrival: BookingArrival | None) -> bool:
    """Support rejected a no-show or a no-fault request on this booking: a late report
    mustn't reopen a free cancel they closed (review R1-2)."""
    if arrival is not None and arrival.no_show_decision:
        return True
    return booking.no_fault_requests.filter(decision=NoFaultRequest.Decision.REJECTED).exists()


def can_answer_late(
    booking: Booking, arrival: BookingArrival | None, now: dt.datetime | None = None
) -> bool:
    """The helper reported running late, the poster hasn't kept the booking since, and
    `late_answer_until` (the new ETA, at least 30 minutes after the report) hasn't come:
    from then the helper may be at work without having tapped, so a free cancel is closed
    and the no-show question is the poster's route."""
    return (
        arrival is not None
        and _held(booking)
        and arrival.late_reported_at is not None
        and arrival.late_accepted_at is None
        and arrival.late_answer_until is not None
        and _now(now) < arrival.late_answer_until
        and not _here(arrival)
        and not _support_decided(booking, arrival)
    )


def respond_to_late(
    poster: User, booking_id: Any, *, keep: bool, now: dt.datetime | None = None
) -> Booking:
    """After "Running late": keep the booking, or cancel for free (module docstring)."""
    now = _now(now)
    with transaction.atomic():
        booking, arrival = _lock(poster, booking_id, ActorKind.POSTER)
        if not can_answer_late(booking, arrival, now):
            raise _not_allowed("There is no late arrival to answer.")
        if not keep:
            no_fault.cancel_for_late_helper(booking, poster, now)
            booking.refresh_from_db()
            return booking
        arrival.late_accepted_at = now
        _record(
            booking,
            arrival,
            Step.LATE_ACCEPTED,
            ["late_accepted_at"],
            actor_kind=ActorKind.POSTER,
            actor=poster,
            now=now,
        )
    return booking


def no_show_due(booking: Booking, arrival: BookingArrival | None) -> dt.datetime | None:
    """When the poster may answer "No, they didn't come" (and, with no tap from the helper,
    is asked); None once the poster said they came or support decided (module docstring)."""
    if arrival is None:
        return booking.slot_start + PROMPT_AFTER_START + NO_REPLY_WINDOW
    if arrival.poster_confirmed_came_at or arrival.no_show_decision:
        # The poster said they came, or support found the helper was there: the poster
        # can't report a no-show (again).
        return None
    latest = booking.slot_start + NO_SHOW_DUE_LATEST
    if arrival.arrived_at is not None:
        # "I've arrived" is evidence, not the end of the poster's route (review R1, MAJOR
        # 1): a wrong tap is answered by the poster's no and the 24 h contest.
        return min(max(booking.slot_start, arrival.arrived_at) + NO_REPLY_WINDOW, latest)
    if arrival.on_my_way_at is None and arrival.late_reported_at is None:
        return booking.slot_start + PROMPT_AFTER_START + NO_REPLY_WINDOW
    expected = max(booking.slot_start, arrival.eta_at or booking.slot_start)
    return min(expected + NO_REPLY_WINDOW, latest)


def can_confirm_came(
    booking: Booking, arrival: BookingArrival | None, now: dt.datetime | None = None
) -> bool:
    """The poster may say "Yes, they're here" from the start until they said so."""
    return (
        _held(booking)
        and _now(now) >= booking.slot_start
        and (arrival is None or arrival.poster_confirmed_came_at is None)
    )


def can_confirm_no_show(
    booking: Booking, arrival: BookingArrival | None, now: dt.datetime | None = None
) -> bool:
    due = no_show_due(booking, arrival)
    return _held(booking) and due is not None and _now(now) >= due


def confirm_helper_came(
    poster: User, booking_id: Any, *, came: bool, now: dt.datetime | None = None
) -> Booking:
    """The poster's answer to "Did your helper come?". Yes ends the timeline. No, once
    `no_show_due` passed, is the helper cancellation with a NO_SHOW fault: a full refund,
    the EUR 5 credit under the late window (bookings/cancellation.py). The helper is told."""
    now = _now(now)
    with transaction.atomic():
        booking, arrival = _lock(poster, booking_id, ActorKind.POSTER)
        if came:
            if not can_confirm_came(booking, arrival, now):
                raise _not_allowed("There is nothing to confirm.")
            arrival.poster_confirmed_came_at = now
            _record(
                booking,
                arrival,
                Step.CAME_CONFIRMED,
                ["poster_confirmed_came_at"],
                actor_kind=ActorKind.POSTER,
                actor=poster,
                now=now,
            )
            return booking
        if not can_confirm_no_show(booking, arrival, now):
            raise _not_allowed("It is too early to say your helper didn't come.")
        outcome = _no_show_outcome(booking, now)
        booking = state_machine.transition(
            booking.pk,
            S.CANCEL_REQUESTED,
            actor_kind=ActorKind.POSTER,
            actor=poster,
            data={"reason": NO_SHOW_REASON, "case": outcome.case.value},
            now=now,
        )
        booking.cancel_reason = NO_SHOW_REASON
        booking.save(update_fields=["cancel_reason", "updated_at"])
        arrival.no_show_confirmed_at = now
        arrival.no_show_contest_by = now + NO_SHOW_CONTEST_WINDOW
        _record(
            booking,
            arrival,
            Step.NO_SHOW_REPORTED,
            ["no_show_confirmed_at", "no_show_contest_by"],
            actor_kind=ActorKind.POSTER,
            actor=poster,
            now=now,
        )
    return booking


def _no_show_outcome(booking: Booking, said_at: dt.datetime) -> Outcome:
    """The helper cancellation as of when the poster said the helper didn't come: a full
    refund, and the EUR 5 credit if that was under the late window (bookings/cancellation.py)."""
    try:
        return cancellation.cancellation_outcome(
            cancelled_by=Party.HELPER,
            no_fault_reason=None,
            amount_cents=booking.amount_cents,
            fee_cents=booking.fee_cents,
            fee_percent=booking.fee_percent,
            accepted_at=booking.created_at,
            slot_start=booking.slot_start,
            now=said_at,
            helper_is_trader=helper_is_trader(booking),
        )
    except NotACancellation as refused:
        raise ApiError(refused.error_code, "This booking can't be cancelled.") from None


def no_show_preview(booking: Booking, now: dt.datetime | None = None) -> Outcome | None:
    """What the poster would get back if they said the helper didn't come now."""
    try:
        return _no_show_outcome(booking, _now(now))
    except ApiError:
        return None


def no_show_reported(booking: Booking, arrival: BookingArrival | None) -> bool:
    """The poster said the helper didn't come and it isn't settled or decided yet."""
    return (
        arrival is not None
        and booking.status == S.CANCEL_REQUESTED
        and booking.cancel_reason == NO_SHOW_REASON
        and arrival.no_show_confirmed_at is not None
        and arrival.no_show_settled_at is None
        and not arrival.no_show_decision
    )


def can_contest_no_show(
    booking: Booking, arrival: BookingArrival | None, now: dt.datetime | None = None
) -> bool:
    return (
        no_show_reported(booking, arrival)
        and arrival is not None
        and arrival.no_show_contested_at is None
        and arrival.no_show_contest_by is not None
        and _now(now) < arrival.no_show_contest_by
    )


def contest_no_show(helper: User, booking_id: Any, *, now: dt.datetime | None = None) -> Booking:
    """ "I was there": within 24 h of the poster's no, the helper sends it to support, who
    decide from the taps, the timeline and booking_events (`decide_no_show`). Nothing is
    refunded or counted against the helper until then."""
    now = _now(now)
    with transaction.atomic():
        booking, arrival = _lock(helper, booking_id, ActorKind.HELPER)
        if not can_contest_no_show(booking, arrival, now):
            raise _not_allowed("This can't be contested now.")
        arrival.no_show_contested_at = now
        _record(
            booking,
            arrival,
            Step.NO_SHOW_CONTESTED,
            ["no_show_contested_at"],
            actor_kind=ActorKind.HELPER,
            actor=helper,
            now=now,
        )
        state_machine.flag_for_support(booking, NO_SHOW_CONTESTED_FLAG, {})
    return booking


def settle_uncontested_no_shows(now: dt.datetime | None = None) -> int:
    """Beat task body: each no-show the helper didn't contest within 24 h becomes the helper
    cancellation: the refund opens and the NO_SHOW fault is recorded. Idempotent. A booking
    that fails is retried with backoff and never holds up the others (review R1-1)."""
    now = _now(now)
    due = list(
        BookingArrival.objects.filter(
            Q(no_show_settle_retry_at__isnull=True) | Q(no_show_settle_retry_at__lte=now),
            no_show_contest_by__isnull=False,
            no_show_contest_by__lte=now,
            no_show_contested_at__isnull=True,
            no_show_settled_at__isnull=True,
            no_show_decision="",
            # A booking that left the no-show another way (a chargeback) mustn't hold a
            # slot forever (review R1, MINOR 2).
            booking__status=S.CANCEL_REQUESTED,
            booking__cancel_reason=NO_SHOW_REASON,
        )
        .order_by("no_show_contest_by")
        .values_list("booking_id", flat=True)[:200]
    )
    settled = 0
    for booking_id in due:
        try:
            with transaction.atomic():
                booking = Booking.objects.select_for_update().get(pk=booking_id)
                arrival = _arrival(booking)
                if not no_show_reported(booking, arrival) or arrival.no_show_contested_at:
                    continue
                if arrival.no_show_contest_by is None or arrival.no_show_contest_by > now:
                    continue
                _settle_no_show(booking, arrival, now, actor_kind=ActorKind.SYSTEM, actor=None)
        except Exception:
            # Most failures are passing (a lock wait, a deadlock): try again later.
            logger.exception(
                "bookings: settling an uncontested no-show failed",
                extra={"booking.id": str(booking_id)},
            )
            _note_settle_failure(booking_id, now)
            continue
        settled += 1
    return settled


def settle_retry_delay(attempts: int) -> dt.timedelta:
    """The wait after the `attempts`-th failure: 15 min, 30 min, 1 h, … at most 6 h."""
    return min(SETTLE_RETRY_BASE * 2 ** max(attempts - 1, 0), SETTLE_RETRY_MAX)


def _note_settle_failure(booking_id: Any, now: dt.datetime) -> None:
    """Count the failure and schedule the next try; at `SETTLE_ALERT_AFTER` failures, flag
    support once (a Sentry money alert with the booking ID only, bookings/alerts.py)."""
    try:
        with transaction.atomic():
            booking = Booking.objects.select_for_update().get(pk=booking_id)
            arrival = _arrival(booking)
            arrival.no_show_settle_attempts += 1
            arrival.no_show_settle_retry_at = now + settle_retry_delay(
                arrival.no_show_settle_attempts
            )
            arrival.save(update_fields=["no_show_settle_attempts", "no_show_settle_retry_at"])
            if arrival.no_show_settle_attempts == SETTLE_ALERT_AFTER:
                state_machine.flag_for_support(
                    booking,
                    NO_SHOW_SETTLE_FAILED_FLAG,
                    {"attempts": arrival.no_show_settle_attempts},
                )
    except Exception:
        # No backoff or flag could be written: alert support at once (payments review
        # MINOR 4).
        logger.exception(
            "bookings: recording a failed no-show settlement failed",
            extra={"booking.id": str(booking_id)},
        )
        money_alert(
            "An uncontested no-show could not be settled or flagged",
            booking_id=booking_id,
            reason=NO_SHOW_SETTLE_FAILED_FLAG,
        )


def settle_failed(arrival: BookingArrival | None) -> bool:
    """The sweep has failed to settle this uncontested no-show at least once."""
    return (
        arrival is not None
        and arrival.no_show_contested_at is None
        and arrival.no_show_settle_attempts > 0
    )


def awaiting_support(booking: Booking) -> bool:
    """A no-show support may decide: the helper contested it, or the sweep failed to settle
    it (review R1-1)."""
    arrival = BookingArrival.objects.filter(booking=booking).first()
    return (
        no_show_reported(booking, arrival)
        and arrival is not None
        and (arrival.no_show_contested_at is not None or settle_failed(arrival))
    )


def decide_no_show(
    staff: User, booking_id: Any, *, uphold: bool, now: dt.datetime | None = None
) -> Booking:
    """Support decides a contested no-show, or one the sweep failed to settle (review R1-1):
    uphold (the helper cancellation: the refund and the NO_SHOW fault) or reject (the
    helper was there: back to FUNDS_HELD, and the poster can't report a no-show again).
    Support and finance only, like no-fault decisions."""
    if not can(staff, Capability.STRIPE_IDS):
        raise _not_allowed("Support or finance only.")
    now = _now(now)
    with transaction.atomic():
        try:
            booking = Booking.objects.select_for_update().get(pk=_pk(booking_id))
        except Booking.DoesNotExist:
            raise ApiError(ErrorCode.BOOKING_NOT_FOUND, "No such booking.") from None
        arrival = _arrival(booking)
        decidable = arrival.no_show_contested_at is not None or settle_failed(arrival)
        if not no_show_reported(booking, arrival) or not decidable:
            raise _not_allowed("No no-show waits for a decision.")
        # Uncontested, the 24 h already made it the helper cancellation (rule g): support
        # only finishes the settling the sweep couldn't (payments review MINOR 1).
        if not uphold and arrival.no_show_contested_at is None:
            raise _not_allowed("An uncontested no-show can only be upheld.")
        arrival.no_show_decision = "UPHELD" if uphold else "REJECTED"
        arrival.no_show_decided_at = now
        arrival.no_show_decided_by = staff
        arrival.save(update_fields=["no_show_decision", "no_show_decided_at", "no_show_decided_by"])
        if uphold:
            _settle_no_show(
                booking,
                arrival,
                now,
                actor_kind=ActorKind.ADMIN,
                actor=staff,
                step=Step.NO_SHOW_UPHELD,
            )
            return booking
        booking.cancel_reason = ""
        booking.save(update_fields=["cancel_reason", "updated_at"])
        booking = state_machine.transition(
            booking.pk,
            S.FUNDS_HELD,
            actor_kind=ActorKind.ADMIN,
            actor=staff,
            data={"reason": "no_show_rejected"},
            now=now,
        )
        _record(
            booking,
            arrival,
            Step.NO_SHOW_REJECTED,
            [],
            actor_kind=ActorKind.ADMIN,
            actor=staff,
            now=now,
        )
    return booking


def _settle_no_show(
    booking: Booking,
    arrival: BookingArrival,
    now: dt.datetime,
    *,
    actor_kind: ActorKind,
    actor: User | None,
    step: Step = Step.NO_SHOW_CONFIRMED,
) -> None:
    said_at = arrival.no_show_confirmed_at or now
    outcome = _no_show_outcome(booking, said_at)
    helper_cancel.settle_helper_fault(
        booking, outcome, HelperFault.Kind.NO_SHOW, now, tell_poster=False
    )
    arrival.no_show_settled_at = now
    _record(
        booking,
        arrival,
        step,
        ["no_show_settled_at"],
        actor_kind=actor_kind,
        actor=actor,
        now=now,
    )


# --- Early start. ---


def early_start_pending(
    booking: Booking, arrival: BookingArrival | None, now: dt.datetime | None = None
) -> bool:
    """The helper's "Start now" waits for the poster, and the booked start hasn't come."""
    return (
        arrival is not None
        and _held(booking)
        and arrival.early_start_requested_at is not None
        and arrival.early_start_confirmed_at is None
        and arrival.early_start_declined_at is None
        and arrival.early_start_until == booking.slot_start
        and _now(now) < booking.slot_start
    )


def request_early_start(
    helper: User, booking_id: Any, *, now: dt.datetime | None = None
) -> Booking:
    """ "Start now": an arrived helper asks to start before the booked time."""
    now = _now(now)
    with transaction.atomic():
        booking, arrival = _lock(helper, booking_id, ActorKind.HELPER)
        _taps_open(booking, now)
        if arrival.arrived_at is None:
            raise _not_allowed("Tap I've arrived first.")
        if now >= booking.slot_start or arrival.early_start_confirmed_at is not None:
            raise _not_allowed("The job has already started.")
        if early_start_pending(booking, arrival, now):
            raise _not_allowed("You already asked to start now.")
        arrival.early_start_requested_at = now
        arrival.early_start_until = booking.slot_start
        arrival.early_start_declined_at = None
        _record(
            booking,
            arrival,
            Step.EARLY_START_REQUESTED,
            ["early_start_requested_at", "early_start_until", "early_start_declined_at"],
            actor_kind=ActorKind.HELPER,
            actor=helper,
            now=now,
        )
    return booking


# The catalogue key the poster is shown before confirming an early start: with a trader
# helper still inside the 14-day period, the copy says the withdrawal right stays (rule g).
EARLY_START_BODY = "arrival.earlyStartBody"
EARLY_START_BODY_WITHDRAWAL = "arrival.earlyStartBodyWithdrawal"


def early_start_wording(booking: Booking, now: dt.datetime | None = None) -> str:
    """Which early-start copy the poster sees (and the event records, review R1-5)."""
    if cancel.withdrawal_available(booking, now):
        return EARLY_START_BODY_WITHDRAWAL
    return EARLY_START_BODY


def respond_to_early_start(
    poster: User,
    booking_id: Any,
    *,
    confirm: bool,
    locale: str | None = None,
    now: dt.datetime | None = None,
) -> Booking:
    """The poster confirms (the booked start moves to now: no cancel from here, the
    dispute route instead) or declines (the booked time stays). The confirmation's event
    records what the poster was shown: the copy's key, the screen's `locale` (the account's
    if not sent) and the CANCELLATION version accepted at checkout (review R1-5; no
    separate consent record, manager decision)."""
    now = _now(now)
    with transaction.atomic():
        booking, arrival = _lock(poster, booking_id, ActorKind.POSTER)
        if not early_start_pending(booking, arrival, now):
            raise _not_allowed("There is no request to start now.")
        if not confirm:
            arrival.early_start_declined_at = now
            _record(
                booking,
                arrival,
                Step.EARLY_START_DECLINED,
                ["early_start_declined_at"],
                actor_kind=ActorKind.POSTER,
                actor=poster,
                now=now,
            )
            return booking
        shown = early_start_wording(booking, now)
        booked = booking.slot_start
        booking.slot_start = now
        booking.save(update_fields=["slot_start", "updated_at"])
        arrival.early_start_confirmed_at = now
        # Confirming the start is the poster saying the helper is there: it ends the
        # no-show route like their "Yes" does.
        arrival.poster_confirmed_came_at = arrival.poster_confirmed_came_at or now
        _record(
            booking,
            arrival,
            Step.EARLY_START_CONFIRMED,
            ["early_start_confirmed_at", "poster_confirmed_came_at"],
            actor_kind=ActorKind.POSTER,
            actor=poster,
            now=now,
            data={
                "slot_start_from": booked.isoformat(),
                "slot_start_to": now.isoformat(),
                "shown": shown,
                "locale": locale or poster.locale,
                "cancellation_version": booking.cancellation_version,
            },
        )
        enqueue(
            state_machine.UPDATED_TOPIC,
            aggregate=booking,
            payload={"booking_id": str(booking.pk), "status": booking.status},
        )
    return booking


# --- The timeline (Celery Beat). ---


def run_timeline(now: dt.datetime | None = None) -> int:
    """Beat task body: send each reminder, prompt and question that is due, once.
    Returns how many went out."""
    now = _now(now)
    candidates = list(
        Booking.objects.filter(
            status=S.FUNDS_HELD,
            task_reopened_at__isnull=True,
            slot_start__lte=now + REMINDER_EARLY,
            slot_start__gt=now - dt.timedelta(days=1),
        )
        # Only bookings with a step still to come (review R1-7): not once the helper is
        # there or the poster has been asked.
        .exclude(arrival__arrived_at__isnull=False)
        .exclude(arrival__poster_confirmed_came_at__isnull=False)
        .exclude(arrival__poster_asked_at__isnull=False)
        .order_by("slot_start")
        .values_list("pk", flat=True)[:500]
    )
    sent = 0
    for pk in candidates:
        try:
            with transaction.atomic():
                booking = Booking.objects.select_for_update().get(pk=pk)
                if not _held(booking):
                    continue
                arrival = _arrival(booking)
                step = _due_step(booking, arrival, now)
                if step is None:
                    continue
                _send(booking, arrival, step, now)
        except Exception:
            # One booking failing mustn't stop the others' reminders; the next run retries.
            logger.exception(
                "bookings: an arrival timeline step failed", extra={"booking.id": str(pk)}
            )
            continue
        sent += 1
    return sent


def _due_step(booking: Booking, arrival: BookingArrival, now: dt.datetime) -> Step | None:
    if _here(arrival):
        return None
    left = booking.slot_start - now
    on_the_way = arrival.on_my_way_at is not None or arrival.late_reported_at is not None
    if left > dt.timedelta(0):
        if on_the_way:
            return None
        if REMINDER_LATE_FLOOR < left <= REMINDER_LATE and arrival.reminder_30m_sent_at is None:
            return Step.REMINDER_30M
        if (
            REMINDER_EARLY_FLOOR < left <= REMINDER_EARLY
            and arrival.reminder_2h_sent_at is None
            and arrival.reminder_30m_sent_at is None
        ):
            return Step.REMINDER_2H
        return None
    if (
        not on_the_way
        and arrival.on_the_way_prompt_sent_at is None
        and now >= booking.slot_start + PROMPT_AFTER_START
    ):
        return Step.ON_THE_WAY_PROMPT
    if arrival.poster_asked_at is None and can_confirm_no_show(booking, arrival, now):
        return Step.ASK_POSTER
    return None


_SENT_FIELD = {
    Step.REMINDER_2H: "reminder_2h_sent_at",
    Step.REMINDER_30M: "reminder_30m_sent_at",
    Step.ON_THE_WAY_PROMPT: "on_the_way_prompt_sent_at",
    Step.ASK_POSTER: "poster_asked_at",
}


def _send(booking: Booking, arrival: BookingArrival, step: Step, now: dt.datetime) -> None:
    field = _SENT_FIELD[step]
    setattr(arrival, field, now)
    _record(booking, arrival, step, [field], actor_kind=ActorKind.SYSTEM, actor=None, now=now)
