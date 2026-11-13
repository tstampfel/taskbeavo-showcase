"""GraphQL types of the day of the job (bookings/arrival.py, BEAV-124): what both sides of
a booking see of the helper's taps, the poster's answers and an early start. Times and
flags only; the `can*` flags say which buttons the viewer gets now."""

import datetime as dt

import strawberry
from django.utils import timezone

from accounts.models import User
from bookings import arrival
from bookings.models import Booking, BookingArrival


@strawberry.type(
    name="BookingArrival",
    description=(
        "The day of the job: the helper's On my way, Running late and I've arrived taps, "
        "the poster's answers and an early start. Evidence for no-show handling; the taps "
        "never change who pays what on their own."
    ),
)
class BookingArrivalType:
    id: strawberry.ID = strawberry.field(description="The booking's ID (hard rule d).")
    on_my_way_at: dt.datetime | None
    eta_at: dt.datetime | None = strawberry.field(
        description="The helper's latest arrival estimate (On my way or Running late)."
    )
    late_reported_at: dt.datetime | None
    late_accepted_at: dt.datetime | None = strawberry.field(
        description="The poster kept the booking after the late report."
    )
    arrived_at: dt.datetime | None
    poster_confirmed_came_at: dt.datetime | None
    no_show_confirmed_at: dt.datetime | None
    on_the_way_prompt_sent_at: dt.datetime | None = strawberry.field(
        description="When the helper was asked 'Are you on your way?' (15 min after the start)."
    )
    poster_asked_at: dt.datetime | None = strawberry.field(
        description="When the poster was asked 'Did your helper come?'."
    )
    no_show_due_at: dt.datetime | None = strawberry.field(
        description=(
            "When the poster may say the helper didn't come: 60 min after the start with no "
            "word from the helper, or 45 min after the start or the helper's ETA, whichever "
            "is later; after 'I've arrived', 45 min after the start or the tap. Null once the "
            "poster said they came."
        )
    )
    no_show_contest_by: dt.datetime | None = strawberry.field(
        description=(
            "After the poster said the helper didn't come: until then the helper may answer "
            "'I was there' (24 hours). The refund waits until then."
        )
    )
    no_show_contested_at: dt.datetime | None
    no_show_settled_at: dt.datetime | None = strawberry.field(
        description="The no-show became the helper cancellation (uncontested, or upheld)."
    )
    no_show_awaiting_support: bool = strawberry.field(
        description="The helper contested the no-show and support is deciding."
    )
    no_show_rejected: bool = strawberry.field(
        description="Support found the helper was there; the booking continues."
    )
    no_show_refund_cents: int | None = strawberry.field(
        description=(
            "For the poster while 'No, they didn't come' is offered: what they would get back."
        )
    )
    no_show_credit_cents: int | None = strawberry.field(
        description="For the poster, with it: TaskBeavo's credit on top (EUR 5 or 0)."
    )
    early_start_requested_at: dt.datetime | None
    early_start_confirmed_at: dt.datetime | None
    early_start_declined_at: dt.datetime | None
    early_start_pending: bool = strawberry.field(
        description="The helper's Start now waits for the poster (until the booked start)."
    )
    can_tap: bool = strawberry.field(
        description=(
            "For the helper: On my way, Running late and I've arrived are open (the money is "
            "held, from 6 hours before the start, until they arrived)."
        )
    )
    can_request_early_start: bool = strawberry.field(
        description="For the helper: Start now is offered (arrived, before the booked start)."
    )
    can_answer_late: bool = strawberry.field(
        description="For the poster: keep the booking or cancel for free after Running late."
    )
    can_confirm_came: bool = strawberry.field(
        description="For the poster: 'Yes, they're here' is offered (after the start)."
    )
    can_confirm_no_show: bool = strawberry.field(
        description="For the poster: 'No, they didn't come' is offered now (`noShowDueAt`)."
    )
    can_contest_no_show: bool = strawberry.field(
        description="For the helper: 'I was there' is offered (within `noShowContestBy`)."
    )
    early_start_withdrawal: bool = strawberry.field(
        description=(
            "For the poster with an early start to answer: the helper is a registered "
            "business still inside the 14-day withdrawal period, so the screen shows the "
            "copy that keeps the withdrawal right (arrival.earlyStartBodyWithdrawal)."
        )
    )
    can_answer_early_start: bool = strawberry.field(
        description="For the poster: confirm or decline the helper's Start now."
    )

    @classmethod
    def of(cls, booking: Booking, viewer: User) -> "BookingArrivalType":
        """Database reads: call it from sync code."""
        record = BookingArrival.objects.filter(booking=booking).first()
        now = timezone.now()
        is_poster = booking.poster_id == viewer.pk
        is_helper = booking.helper_id == viewer.pk
        held = booking.status == Booking.Status.FUNDS_HELD and booking.task_reopened_at is None
        here = record is not None and (
            record.arrived_at is not None or record.poster_confirmed_came_at is not None
        )
        taps_open = held and now >= booking.slot_start - arrival.TAPS_OPEN_BEFORE
        pending = arrival.early_start_pending(booking, record, now)
        can_no_show = is_poster and arrival.can_confirm_no_show(booking, record, now)
        preview = arrival.no_show_preview(booking, now) if can_no_show else None

        def at(name: str) -> dt.datetime | None:
            return getattr(record, name) if record is not None else None

        return cls(
            id=strawberry.ID(str(booking.pk)),
            on_my_way_at=at("on_my_way_at"),
            eta_at=at("eta_at"),
            late_reported_at=at("late_reported_at"),
            late_accepted_at=at("late_accepted_at"),
            arrived_at=at("arrived_at"),
            poster_confirmed_came_at=at("poster_confirmed_came_at"),
            no_show_confirmed_at=at("no_show_confirmed_at"),
            on_the_way_prompt_sent_at=at("on_the_way_prompt_sent_at"),
            poster_asked_at=at("poster_asked_at"),
            no_show_due_at=arrival.no_show_due(booking, record) if held else None,
            no_show_contest_by=at("no_show_contest_by"),
            no_show_contested_at=at("no_show_contested_at"),
            no_show_settled_at=at("no_show_settled_at"),
            no_show_awaiting_support=arrival.awaiting_support(booking),
            no_show_rejected=record is not None and record.no_show_decision == "REJECTED",
            no_show_refund_cents=preview.refund_cents if preview else None,
            no_show_credit_cents=preview.poster_credit_cents if preview else None,
            early_start_requested_at=at("early_start_requested_at"),
            early_start_confirmed_at=at("early_start_confirmed_at"),
            early_start_declined_at=at("early_start_declined_at"),
            early_start_pending=pending,
            can_tap=is_helper
            and taps_open
            and (record is None or record.arrived_at is None)
            and not here,
            can_request_early_start=is_helper
            and taps_open
            and record is not None
            and record.arrived_at is not None
            and record.early_start_confirmed_at is None
            and now < booking.slot_start
            and not pending,
            can_answer_late=is_poster and arrival.can_answer_late(booking, record, now),
            can_confirm_came=is_poster and arrival.can_confirm_came(booking, record, now),
            can_confirm_no_show=can_no_show,
            can_contest_no_show=is_helper and arrival.can_contest_no_show(booking, record, now),
            early_start_withdrawal=is_poster
            and pending
            and arrival.early_start_wording(booking, now) == arrival.EARLY_START_BODY_WITHDRAWAL,
            can_answer_early_start=is_poster and pending,
        )
