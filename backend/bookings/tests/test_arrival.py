"""The day of the job (BEAV-124; PLAN.md, "Helper no-show and late arrival"; hard rules c,
g and k): On my way, Running late, I've arrived, the reminders and the no-show timeline on
a frozen clock (every call gets a fixed `now`), the poster's confirmed no-show running the
helper cancellation from BEAV-123, the free cancel after Running late (user decision
2026-11-08), and an early start both sides agree to."""

import datetime as dt

import pytest
from django.test import Client
from django.utils import timezone

from accounts.tests.test_schema import error_codes, gql
from bookings import arrival, cancel, no_fault, state_machine
from bookings.arrival import Step
from bookings.cancel import PosterCancelReason
from bookings.cancellation import NoFaultReason
from bookings.models import (
    Booking,
    BookingArrival,
    BookingEvent,
    CancellationSettlement,
    HelperFault,
    HelperReviewItem,
    NoFaultRequest,
)
from bookings.tests.fakes import FakeGateway
from bookings.tests.test_checkout import (  # noqa: F401 (fixtures)
    _fresh_rate_limits,
    legal_documents,
    post_event,
    publish,
    webhook_secret,
)
from bookings.tests.test_helper_cancel import booking_for
from bookings.tests.test_poster_cancel import code, helper, paid, poster, starting, token
from core.errors import ApiError, ErrorCode
from core.models import ActorKind, Outbox
from notifications.models import Delivery
from staff.roles import Role
from staff.tests.factories import make_staff

pytestmark = pytest.mark.django_db

S = Booking.Status
H = dt.timedelta(hours=1)
M = dt.timedelta(minutes=1)


def booked(client: Client, starts_in: dt.timedelta = 48 * H) -> tuple[Booking, dt.datetime]:
    """A held booking and its booked start, `starts_in` after acceptance."""
    booking = starting(paid(client), starts_in)
    return booking, booking.slot_start


def steps(booking: Booking) -> list[str]:
    rows = Outbox.objects.filter(topic=arrival.ARRIVAL_TOPIC, aggregate_id=str(booking.pk))
    return [row.payload["step"] for row in rows.order_by("pk")]


def arrival_of(booking: Booking) -> BookingArrival:
    return BookingArrival.objects.get(booking=booking)


def kinds_for(user_id: int) -> set[str]:
    return set(Delivery.objects.filter(user_id=user_id).values_list("kind", flat=True))


# --- The timeline on a frozen clock. ---


def test_the_timeline_with_no_word_from_the_helper(client: Client, gateway: FakeGateway) -> None:
    booking, start = booked(client)

    def tick(at: dt.datetime) -> int:
        return arrival.run_timeline(at)

    assert tick(start - 3 * H) == 0
    assert tick(start - 2 * H + M) == 1
    assert tick(start - 2 * H + 2 * M) == 0
    assert tick(start - 29 * M) == 1
    assert tick(start - 10 * M) == 0
    assert tick(start + 14 * M) == 0
    assert tick(start + 15 * M) == 1
    assert tick(start + 59 * M) == 0
    assert tick(start + 60 * M) == 1
    assert tick(start + 3 * H) == 0

    assert steps(booking) == ["REMINDER_2H", "REMINDER_30M", "ON_THE_WAY_PROMPT", "ASK_POSTER"]
    record = arrival_of(booking)
    assert record.on_the_way_prompt_sent_at == start + 15 * M
    assert record.poster_asked_at == start + 60 * M
    # Each one is evidence in booking_events, from the system.
    sent = BookingEvent.objects.filter(booking=booking, type="on_the_way_prompt").get()
    assert sent.actor_kind == ActorKind.SYSTEM


def test_the_reminders_go_to_the_helper_and_the_question_to_the_poster(
    client: Client, gateway: FakeGateway
) -> None:
    booking, start = booked(client)
    for at in (start - 2 * H + M, start - 29 * M, start + 15 * M, start + 60 * M):
        arrival.run_timeline(at)

    publish(arrival.ARRIVAL_TOPIC, booking)

    assert kinds_for(booking.helper_id) == {
        "booking.reminder_2h",
        "booking.reminder_30m",
        "booking.on_the_way_prompt",
    }
    assert kinds_for(booking.poster_id) == {"booking.did_helper_come"}


def test_a_late_booking_skips_the_2_hour_reminder(client: Client, gateway: FakeGateway) -> None:
    booking, start = booked(client, starts_in=2 * H)

    arrival.run_timeline(start - 80 * M)
    arrival.run_timeline(start - 25 * M)

    assert steps(booking) == ["REMINDER_30M"]


def test_on_my_way_stops_the_reminders_and_the_prompt(client: Client, gateway: FakeGateway) -> None:
    booking, start = booked(client)
    arrival.on_my_way(helper(booking), booking.pk, 20, now=start - 40 * M)

    for at in (start - 29 * M, start + 15 * M, start + 30 * M):
        assert arrival.run_timeline(at) == 0
    # Asked 45 minutes after the ETA (start - 20 min), no sooner than 45 after the start.
    assert arrival.run_timeline(start + 44 * M) == 0
    assert arrival.run_timeline(start + 45 * M) == 1
    assert steps(booking) == ["ON_MY_WAY", "ASK_POSTER"]


def test_arriving_ends_the_prompts_but_not_the_posters_no(
    client: Client, gateway: FakeGateway
) -> None:
    booking, start = booked(client)
    arrival.arrived(helper(booking), booking.pk, now=start - 5 * M)

    for at in (start + 15 * M, start + 60 * M, start + 3 * H):
        assert arrival.run_timeline(at) == 0
    # The tap is evidence only (review R1, MAJOR 1): 45 minutes after the start or the tap,
    # the poster may still say no, and the 24 h contest protects an honest helper.
    record = arrival_of(booking)
    assert arrival.no_show_due(booking, record) == start + 45 * M
    assert not arrival.can_confirm_no_show(booking, record, start + 44 * M)
    assert arrival.can_confirm_came(booking, record, start + 10 * M)

    arrival.confirm_helper_came(poster(booking), booking.pk, came=False, now=start + 45 * M)

    assert arrival_of(booking).no_show_contest_by == start + 45 * M + D


def test_a_late_arrival_tap_waits_45_minutes_but_never_past_the_cap(
    client: Client, gateway: FakeGateway
) -> None:
    booking, start = booked(client)
    arrival.arrived(helper(booking), booking.pk, now=start + 50 * M)
    assert arrival.no_show_due(booking, arrival_of(booking)) == start + 95 * M
    BookingArrival.objects.filter(booking=booking).update(arrived_at=start + 6 * H)
    assert arrival.no_show_due(booking, arrival_of(booking)) == start + arrival.NO_SHOW_DUE_LATEST


def test_the_posters_yes_ends_the_no_show_route(client: Client, gateway: FakeGateway) -> None:
    booking, start = booked(client)
    arrival.arrived(helper(booking), booking.pk, now=start - 5 * M)
    arrival.confirm_helper_came(poster(booking), booking.pk, came=True, now=start + 10 * M)
    record = arrival_of(booking)
    assert arrival.no_show_due(booking, record) is None
    assert not arrival.can_confirm_came(booking, record, start + 20 * M)


def test_running_late_moves_the_poster_question_to_45_minutes_after_the_eta(
    client: Client, gateway: FakeGateway
) -> None:
    booking, start = booked(client)
    arrival.run_timeline(start + 15 * M)
    arrival.running_late(helper(booking), booking.pk, 30, now=start + 20 * M)

    record = arrival_of(booking)
    assert record.eta_at == start + 50 * M
    assert arrival.no_show_due(booking, record) == start + 95 * M
    assert arrival.run_timeline(start + 94 * M) == 0
    assert arrival.run_timeline(start + 95 * M) == 1


def test_the_timeline_ignores_bookings_not_held(client: Client, gateway: FakeGateway) -> None:
    booking, start = booked(client)
    cancel.cancel_booking(
        poster(booking), booking.pk, PosterCancelReason.NO_LONGER_NEEDED, now=start - 30 * H
    )

    assert arrival.run_timeline(start + 15 * M) == 0
    assert steps(booking) == []


# --- The helper's taps. ---


def test_on_my_way_records_evidence_and_tells_the_poster(
    client: Client, gateway: FakeGateway
) -> None:
    booking, start = booked(client)

    arrival.on_my_way(helper(booking), booking.pk, 15, now=start - 20 * M)

    record = arrival_of(booking)
    assert (record.on_my_way_at, record.eta_at) == (start - 20 * M, start - 5 * M)
    event = BookingEvent.objects.get(booking=booking, type="on_my_way")
    assert event.data == {"step": "ON_MY_WAY", "eta_minutes": 15}
    assert event.actor_kind == ActorKind.HELPER
    publish(arrival.ARRIVAL_TOPIC, booking)
    assert kinds_for(booking.poster_id) == {"booking.helper_on_my_way"}
    assert kinds_for(booking.helper_id) == set()
    # Never a location (hard rule k).
    assert not any("lat" in key or "gps" in key for key in event.data)


def test_only_the_helper_taps_and_only_on_the_day(client: Client, gateway: FakeGateway) -> None:
    booking, start = booked(client)

    with pytest.raises(ApiError) as other:
        arrival.on_my_way(poster(booking), booking.pk, None, now=start - M)
    with pytest.raises(ApiError) as early:
        arrival.arrived(helper(booking), booking.pk, now=start - 7 * H)

    assert code(other) == ErrorCode.BOOKING_NOT_FOUND.value
    assert code(early) == ErrorCode.BOOKING_ACTION_NOT_ALLOWED.value


def test_an_eta_must_be_in_range_and_running_late_after_the_start(
    client: Client, gateway: FakeGateway
) -> None:
    booking, start = booked(client)
    who = helper(booking)

    for minutes in (0, 241):
        with pytest.raises(ApiError) as bad:
            arrival.on_my_way(who, booking.pk, minutes, now=start - M)
        assert code(bad) == ErrorCode.INVALID_ETA.value
    with pytest.raises(ApiError) as before_start:
        arrival.running_late(who, booking.pk, 10, now=start - 30 * M)
    assert code(before_start) == ErrorCode.INVALID_ETA.value


def test_no_taps_after_arriving(client: Client, gateway: FakeGateway) -> None:
    booking, start = booked(client)
    arrival.arrived(helper(booking), booking.pk, now=start)

    for tap in (
        lambda: arrival.arrived(helper(booking), booking.pk, now=start + M),
        lambda: arrival.on_my_way(helper(booking), booking.pk, None, now=start + M),
        lambda: arrival.running_late(helper(booking), booking.pk, 10, now=start + M),
    ):
        with pytest.raises(ApiError) as refused:
            tap()
        assert code(refused) == ErrorCode.BOOKING_ACTION_NOT_ALLOWED.value


# --- The poster's no-show: 24 h to contest, then the helper cancellation from BEAV-123. ---

D = dt.timedelta(days=1)
SEC = dt.timedelta(seconds=1)


def reported(client: Client) -> tuple[Booking, dt.datetime]:
    """A booking whose poster said the helper didn't come, at the start + 60 min."""
    booking, start = booked(client)
    arrival.confirm_helper_came(poster(booking), booking.pk, came=False, now=start + 60 * M)
    booking.refresh_from_db()
    return booking, start + 60 * M


def test_no_waits_24_hours_for_the_helper_before_any_refund(
    client: Client, gateway: FakeGateway
) -> None:
    booking, start = booked(client)
    with pytest.raises(ApiError) as too_early:
        arrival.confirm_helper_came(poster(booking), booking.pk, came=False, now=start + 59 * M)
    assert code(too_early) == ErrorCode.BOOKING_ACTION_NOT_ALLOWED.value

    said = arrival.confirm_helper_came(poster(booking), booking.pk, came=False, now=start + H)

    assert (said.status, said.cancel_reason) == (S.CANCEL_REQUESTED, "NO_SHOW")
    assert arrival_of(booking).no_show_contest_by == start + H + D
    # Nothing moves and nothing counts against the helper yet; the address is gone at once.
    assert not CancellationSettlement.objects.filter(booking=booking).exists()
    assert not HelperFault.objects.filter(booking=booking).exists()
    assert not Booking.objects.get(pk=booking.pk).is_active
    publish(arrival.ARRIVAL_TOPIC, booking)
    assert "booking.no_show_reported" in kinds_for(booking.helper_id)
    assert not Outbox.objects.filter(
        topic="booking.cancelled_by_helper", aggregate_id=str(booking.pk)
    ).exists()


def test_uncontested_after_24_hours_it_is_the_helper_cancellation_with_the_credit(
    client: Client, gateway: FakeGateway
) -> None:
    booking, said_at = reported(client)

    assert arrival.settle_uncontested_no_shows(said_at + D - SEC) == 0
    assert arrival.settle_uncontested_no_shows(said_at + D) == 1
    assert arrival.settle_uncontested_no_shows(said_at + D + H) == 0

    s = CancellationSettlement.objects.get(booking=booking)
    assert (s.case, s.refund_cents, s.helper_compensation_cents, s.kept_fee_cents) == (
        "HELPER_CANCEL",
        booking.amount_cents + booking.fee_cents,
        0,
        0,
    )
    # The credit as of the poster's no (an hour after the start), not of the sweep.
    assert s.poster_credit_cents == 500
    assert HelperFault.objects.get(booking=booking).kind == HelperFault.Kind.NO_SHOW
    assert arrival_of(booking).no_show_settled_at == said_at + D
    publish(arrival.ARRIVAL_TOPIC, booking)
    assert "booking.no_show_confirmed" in kinds_for(booking.helper_id)


def test_the_helper_contests_inside_the_window_and_support_decides(
    client: Client, gateway: FakeGateway
) -> None:
    booking, said_at = reported(client)

    arrival.contest_no_show(helper(booking), booking.pk, now=said_at + D - SEC)

    assert arrival.settle_uncontested_no_shows(said_at + 2 * D) == 0
    assert not CancellationSettlement.objects.filter(booking=booking).exists()
    assert arrival.awaiting_support(booking)
    assert BookingEvent.objects.filter(
        booking=booking, type="support_flag", data__reason="no_show_contested"
    ).exists()
    publish(arrival.ARRIVAL_TOPIC, booking)
    assert "booking.no_show_contested" in kinds_for(booking.poster_id)


def _break_settling(monkeypatch: pytest.MonkeyPatch, *broken_ids: object) -> list[int]:
    """Make settling the given bookings fail; returns a one-item counter of failures."""
    settle = arrival._settle_no_show
    failures = [0]

    def broken(booking: Booking, *args: object, **kwargs: object) -> None:
        if booking.pk in broken_ids:
            failures[0] += 1
            raise RuntimeError("boom")
        settle(booking, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(arrival, "_settle_no_show", broken)
    return failures


def test_a_failed_settlement_never_blocks_the_others_and_is_retried_with_backoff(
    client: Client, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, said_at = reported(client)
    second = booking_for(client, helper(first), ("luka", "ante"))
    BookingArrival.objects.filter(booking=first).update(no_show_contest_by=said_at)
    arrival.confirm_helper_came(
        poster(second), second.pk, came=False, now=second.slot_start + 2 * H
    )
    _break_settling(monkeypatch, first.pk)
    later = second.slot_start + 2 * H + D

    assert arrival.settle_uncontested_no_shows(later) == 1
    assert CancellationSettlement.objects.filter(booking=second).exists()
    record = arrival_of(first)
    assert (record.no_show_settle_attempts, record.no_show_settle_retry_at) == (1, later + 15 * M)
    # Not before its retry time; then again, and a passing error clears on the next try.
    assert arrival.settle_uncontested_no_shows(later + 14 * M) == 0
    monkeypatch.undo()
    assert arrival.settle_uncontested_no_shows(later + 15 * M) == 1
    assert CancellationSettlement.objects.filter(booking=first).exists()
    assert not BookingEvent.objects.filter(booking=first, type="support_flag").exists()


def test_repeated_failures_alert_support_once_and_keep_retrying(
    client: Client, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    booking, said_at = reported(client)
    failures = _break_settling(monkeypatch, booking.pk)
    at = said_at + D
    waits = []
    for _ in range(4):
        arrival.settle_uncontested_no_shows(at)
        retry_at = arrival_of(booking).no_show_settle_retry_at
        assert retry_at is not None
        waits.append(retry_at - at)
        at = retry_at

    assert failures[0] == 4
    assert waits == [15 * M, 30 * M, H, 2 * H]
    flags = BookingEvent.objects.filter(
        booking=booking, type="support_flag", data__reason="no_show_settle_failed"
    )
    # Flagged (with the Sentry money alert, bookings/alerts.py) at the third failure only.
    assert flags.count() == 1
    assert flags.get().data["attempts"] == arrival.SETTLE_ALERT_AFTER
    assert arrival.settle_retry_delay(20) == arrival.SETTLE_RETRY_MAX


def test_a_failure_that_cannot_even_be_recorded_still_alerts_support(
    client: Client, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    booking, said_at = reported(client)
    _break_settling(monkeypatch, booking.pk)
    alerts: list[dict[str, object]] = []
    monkeypatch.setattr(arrival, "money_alert", lambda message, **tags: alerts.append(tags))
    monkeypatch.setattr(
        arrival, "settle_retry_delay", lambda attempts: (_ for _ in ()).throw(RuntimeError())
    )

    arrival.settle_uncontested_no_shows(said_at + D)

    assert alerts == [{"booking_id": booking.pk, "reason": "no_show_settle_failed"}]


def test_support_can_settle_an_uncontested_no_show_the_sweep_failed_on(
    client: Client, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    booking, said_at = reported(client)
    staff = make_staff(Role.SUPPORT)
    # Not before the sweep has failed: the 24 h and the sweep are the normal route.
    with pytest.raises(ApiError):
        arrival.decide_no_show(staff, booking.pk, uphold=True, now=said_at + D + M)
    assert not arrival.awaiting_support(booking)
    _break_settling(monkeypatch, booking.pk)
    arrival.settle_uncontested_no_shows(said_at + D)
    assert arrival.awaiting_support(booking)
    monkeypatch.undo()

    # The helper didn't contest it: support can't reject it (rule g).
    with pytest.raises(ApiError):
        arrival.decide_no_show(staff, booking.pk, uphold=False, now=said_at + D + H)
    arrival.decide_no_show(staff, booking.pk, uphold=True, now=said_at + D + H)

    s = CancellationSettlement.objects.get(booking=booking)
    assert (s.case, s.poster_credit_cents) == ("HELPER_CANCEL", 500)
    assert HelperFault.objects.get(booking=booking).kind == HelperFault.Kind.NO_SHOW
    # The sweep has nothing left to do.
    assert arrival.settle_uncontested_no_shows(said_at + 2 * D) == 0


def test_the_admin_queue_and_form_show_a_no_show_the_sweep_failed_on(
    client: Client, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    from django.contrib.admin.sites import site

    from bookings.admin import BookingAdmin, CancellationQueueFilter

    booking, said_at = reported(client)
    _break_settling(monkeypatch, booking.pk)
    arrival.settle_uncontested_no_shows(said_at + D)
    staff = make_staff(Role.SUPPORT, is_superuser=True)
    queue = CancellationQueueFilter(
        None, {"cancellation": "no_show"}, Booking, BookingAdmin(Booking, site)
    )
    assert list(queue.queryset(None, Booking.objects.all())) == [booking]  # type: ignore[arg-type]

    monkeypatch.undo()
    client.force_login(staff)
    page = client.get(f"/admin/bookings/booking/{booking.pk}/change/")
    assert page.status_code == 200
    assert "no-show-decision" in page.content.decode()
    assert 'value="reject"' not in page.content.decode()
    # Failing again from the admin: a message, not a 500, and the money stays held.
    _break_settling(monkeypatch, booking.pk)
    again = client.post(
        f"/admin/bookings/booking/{booking.pk}/no-show-decision/", {"decision": "uphold"}
    )
    assert again.status_code == 302
    assert not CancellationSettlement.objects.filter(booking=booking).exists()
    monkeypatch.undo()
    done = client.post(
        f"/admin/bookings/booking/{booking.pk}/no-show-decision/", {"decision": "uphold"}
    )
    assert done.status_code == 302
    assert CancellationSettlement.objects.filter(booking=booking).exists()


def test_the_contest_closes_at_24_hours(client: Client, gateway: FakeGateway) -> None:
    booking, said_at = reported(client)
    with pytest.raises(ApiError) as closed:
        arrival.contest_no_show(helper(booking), booking.pk, now=said_at + D)
    assert code(closed) == ErrorCode.BOOKING_ACTION_NOT_ALLOWED.value
    with pytest.raises(ApiError) as poster_cannot:
        arrival.contest_no_show(poster(booking), booking.pk, now=said_at + H)
    assert code(poster_cannot) == ErrorCode.BOOKING_NOT_FOUND.value


def test_support_upholds_a_contested_no_show(client: Client, gateway: FakeGateway) -> None:
    booking, said_at = reported(client)
    arrival.contest_no_show(helper(booking), booking.pk, now=said_at + H)

    arrival.decide_no_show(make_staff(Role.SUPPORT), booking.pk, uphold=True, now=said_at + 3 * H)

    s = CancellationSettlement.objects.get(booking=booking)
    assert (s.case, s.poster_credit_cents) == ("HELPER_CANCEL", 500)
    assert HelperFault.objects.get(booking=booking).kind == HelperFault.Kind.NO_SHOW
    assert arrival_of(booking).no_show_decision == "UPHELD"
    # Both sides hear support's decision (review R1, MINOR 5).
    publish(arrival.ARRIVAL_TOPIC, booking)
    assert "booking.no_show_upheld" in kinds_for(booking.poster_id)
    assert "booking.no_show_upheld" in kinds_for(booking.helper_id)


def test_neither_side_can_leave_a_reported_no_show(client: Client, gateway: FakeGateway) -> None:
    booking, said_at = reported(client)
    for actor_kind, actor, target in (
        (ActorKind.POSTER, poster(booking), S.FUNDS_HELD),
        (ActorKind.HELPER, helper(booking), S.DISPUTED),
    ):
        with pytest.raises(ApiError) as refused:
            state_machine.transition(
                booking.pk, target, actor_kind=actor_kind, actor=actor, now=said_at + H
            )
        assert code(refused) == ErrorCode.BOOKING_ACTION_NOT_ALLOWED.value
    booking.refresh_from_db()
    assert booking.status == S.CANCEL_REQUESTED


def test_the_sweep_ignores_a_no_show_that_left_by_another_way(
    client: Client, gateway: FakeGateway
) -> None:
    booking, said_at = reported(client)
    # A chargeback moved it to DISPUTED while the 24 hours ran (review R1, MINOR 2).
    Booking.objects.filter(pk=booking.pk).update(status=S.DISPUTED)

    assert arrival.settle_uncontested_no_shows(said_at + 2 * D) == 0
    assert not CancellationSettlement.objects.filter(booking=booking).exists()


def test_support_rejects_a_contested_no_show_and_the_booking_continues(
    client: Client, gateway: FakeGateway
) -> None:
    booking, said_at = reported(client)
    arrival.contest_no_show(helper(booking), booking.pk, now=said_at + H)

    kept = arrival.decide_no_show(
        make_staff(Role.SUPPORT), booking.pk, uphold=False, now=said_at + 3 * H
    )

    assert (kept.status, kept.cancel_reason) == (S.FUNDS_HELD, "")
    assert not CancellationSettlement.objects.filter(booking=booking).exists()
    assert not HelperFault.objects.filter(booking=booking).exists()
    assert Booking.objects.get(pk=booking.pk).is_active
    # The poster can't say "No" again, and the sweep has nothing to do.
    assert not arrival.can_confirm_no_show(kept, arrival_of(booking), said_at + 4 * H)
    assert arrival.settle_uncontested_no_shows(said_at + 2 * D) == 0
    publish(arrival.ARRIVAL_TOPIC, booking)
    assert "booking.no_show_rejected" in kinds_for(booking.poster_id)
    assert "booking.no_show_rejected" in kinds_for(booking.helper_id)


def test_only_money_roles_decide_and_only_a_contested_no_show(
    client: Client, gateway: FakeGateway
) -> None:
    booking, said_at = reported(client)
    with pytest.raises(ApiError):
        arrival.decide_no_show(make_staff(Role.SUPPORT), booking.pk, uphold=True)
    arrival.contest_no_show(helper(booking), booking.pk, now=said_at + H)
    with pytest.raises(ApiError):
        arrival.decide_no_show(make_staff(Role.TRUST), booking.pk, uphold=True)


def test_a_no_show_never_restricts_the_helper_only_queues_a_human_review(
    client: Client, gateway: FakeGateway
) -> None:
    first, start = booked(client)
    arrival.confirm_helper_came(poster(first), first.pk, came=False, now=start + 2 * H)
    second = booking_for(client, helper(first), ("luka", "ante"))
    arrival.confirm_helper_came(
        poster(second), second.pk, came=False, now=second.slot_start + 2 * H
    )

    arrival.settle_uncontested_no_shows(second.slot_start + 2 * H + D)

    item = HelperReviewItem.objects.get(helper_id=first.helper_id)
    assert item.reviewed_at is None
    assert helper(first).is_active


def test_yes_he_came_ends_the_timeline(client: Client, gateway: FakeGateway) -> None:
    booking, start = booked(client)
    arrival.run_timeline(start + 15 * M)

    arrival.confirm_helper_came(poster(booking), booking.pk, came=True, now=start + 40 * M)

    assert arrival.run_timeline(start + 2 * H) == 0
    with pytest.raises(ApiError):
        arrival.confirm_helper_came(poster(booking), booking.pk, came=False, now=start + 2 * H)


def test_the_helper_cannot_confirm_their_own_no_show(client: Client, gateway: FakeGateway) -> None:
    booking, start = booked(client)
    with pytest.raises(ApiError) as refused:
        arrival.confirm_helper_came(helper(booking), booking.pk, came=False, now=start + 2 * H)
    assert code(refused) == ErrorCode.BOOKING_NOT_FOUND.value


# --- Running late: the poster keeps it or cancels for free. ---


def test_keeping_the_booking_after_running_late(client: Client, gateway: FakeGateway) -> None:
    booking, start = booked(client)
    arrival.running_late(helper(booking), booking.pk, 25, now=start + 15 * M)

    kept = arrival.respond_to_late(poster(booking), booking.pk, keep=True, now=start + 16 * M)

    assert kept.status == S.FUNDS_HELD
    assert arrival_of(booking).late_accepted_at == start + 16 * M
    publish(arrival.ARRIVAL_TOPIC, booking)
    assert "booking.late_arrival_accepted" in kinds_for(booking.helper_id)
    assert "booking.helper_running_late" in kinds_for(booking.poster_id)


def test_the_free_cancel_after_running_late_is_no_fault_at_once(
    client: Client, gateway: FakeGateway
) -> None:
    booking, start = booked(client)
    arrival.running_late(helper(booking), booking.pk, 25, now=start + 15 * M)

    cancelled = arrival.respond_to_late(poster(booking), booking.pk, keep=False, now=start + 16 * M)

    assert cancelled.status == S.CANCEL_REQUESTED
    s = CancellationSettlement.objects.get(booking=booking)
    assert (s.case, s.refund_cents, s.poster_credit_cents) == (
        "NO_FAULT",
        booking.amount_cents + booking.fee_cents,
        0,
    )
    request = NoFaultRequest.objects.get(booking=booking)
    assert (request.reason, request.response) == ("HELPER_LATE", "AGREED")
    assert request.closed_at == start + 16 * M
    assert not HelperFault.objects.filter(booking=booking).exists()
    # The helper no longer sees the exact place (hard rule e).
    assert not Booking.objects.get(pk=booking.pk).is_active


def test_no_free_cancel_without_a_late_report(client: Client, gateway: FakeGateway) -> None:
    booking, start = booked(client)
    with pytest.raises(ApiError) as refused:
        arrival.respond_to_late(poster(booking), booking.pk, keep=False, now=start + 20 * M)
    assert code(refused) == ErrorCode.BOOKING_ACTION_NOT_ALLOWED.value


def test_helper_late_cannot_be_cited_by_hand(client: Client, gateway: FakeGateway) -> None:
    booking, start = booked(client)
    with pytest.raises(ApiError) as refused:
        no_fault.request_no_fault(
            poster(booking), booking.pk, NoFaultReason.HELPER_LATE, now=start - H
        )
    assert code(refused) == ErrorCode.BOOKING_ACTION_NOT_ALLOWED.value


# --- Early start: both sides agree, then the after-start rules apply. ---


def test_a_confirmed_early_start_moves_the_start_and_ends_the_poster_cancel(
    client: Client, gateway: FakeGateway
) -> None:
    booking, start = booked(client)
    who = helper(booking)
    with pytest.raises(ApiError):
        arrival.request_early_start(who, booking.pk, now=start - 40 * M)
    arrival.arrived(who, booking.pk, now=start - 45 * M)
    arrival.request_early_start(who, booking.pk, now=start - 40 * M)
    assert arrival.early_start_pending(booking, arrival_of(booking), start - 39 * M)

    moved = arrival.respond_to_early_start(
        poster(booking), booking.pk, confirm=True, now=start - 38 * M
    )

    assert moved.slot_start == start - 38 * M
    event = BookingEvent.objects.get(booking=booking, type="early_start_confirmed")
    assert event.data["slot_start_from"] == start.isoformat()
    # A private helper: the ordinary copy, in the poster's account language by default.
    assert event.data["shown"] == "arrival.earlyStartBody"
    assert event.data["locale"] == poster(booking).locale
    assert Outbox.objects.filter(topic="booking.updated", aggregate_id=str(booking.pk)).exists()
    with pytest.raises(ApiError) as refused:
        cancel.cancel_booking(
            poster(booking), booking.pk, PosterCancelReason.NO_LONGER_NEEDED, now=start - 30 * M
        )
    assert code(refused) == ErrorCode.WORK_STARTED.value
    # The poster agreed the helper is there: no "No, they didn't come" afterwards.
    record = arrival_of(booking)
    assert arrival.no_show_due(moved, record) is None
    assert not arrival.can_confirm_no_show(moved, record, start + 2 * H)


def test_a_declined_or_lapsed_early_start_keeps_the_booked_time(
    client: Client, gateway: FakeGateway
) -> None:
    booking, start = booked(client)
    who = helper(booking)
    arrival.arrived(who, booking.pk, now=start - 45 * M)
    arrival.request_early_start(who, booking.pk, now=start - 40 * M)

    arrival.respond_to_early_start(poster(booking), booking.pk, confirm=False, now=start - 39 * M)
    booking.refresh_from_db()
    assert booking.slot_start == start

    arrival.request_early_start(who, booking.pk, now=start - 20 * M)
    with pytest.raises(ApiError) as lapsed:
        arrival.respond_to_early_start(poster(booking), booking.pk, confirm=True, now=start)
    assert code(lapsed) == ErrorCode.BOOKING_ACTION_NOT_ALLOWED.value
    booking.refresh_from_db()
    assert booking.slot_start == start


# --- The API. ---

BOOKING_ARRIVAL = """
query($id: ID!) { booking(id: $id) { id arrival {
  canTap canAnswerLate canConfirmNoShow canConfirmCame noShowDueAt etaAt onMyWayAt
  noShowRefundCents noShowCreditCents canContestNoShow
} } }
"""
ON_MY_WAY = """
mutation($id: ID!, $eta: Int) { markOnMyWay(bookingId: $id, etaMinutes: $eta) {
  id arrival { onMyWayAt etaAt canTap }
} }
"""
NO_SHOW = """
mutation($id: ID!) { confirmHelperArrival(bookingId: $id, came: false) {
  id status arrival { noShowContestBy }
} }
"""
CONTEST = """
mutation($id: ID!) { contestNoShow(bookingId: $id) {
  id status arrival { noShowContestedAt noShowAwaitingSupport canContestNoShow }
} }
"""


def test_the_api_flow_on_my_way_and_no_show(client: Client, gateway: FakeGateway) -> None:
    now = timezone.now()
    booking = paid(client)
    Booking.objects.filter(pk=booking.pk).update(created_at=now - 30 * H, slot_start=now + 20 * M)
    booking.refresh_from_db()

    seen = gql(client, BOOKING_ARRIVAL, {"id": str(booking.pk)}, token(helper(booking)))
    assert seen["data"]["booking"]["arrival"]["canTap"] is True
    body = gql(client, ON_MY_WAY, {"id": str(booking.pk), "eta": 15}, token(helper(booking)))
    assert body["data"]["markOnMyWay"]["arrival"]["onMyWayAt"] is not None
    refused = gql(client, ON_MY_WAY, {"id": str(booking.pk)}, token(poster(booking)))
    assert error_codes(refused) == ["BOOKING_NOT_FOUND"]

    poster_view = gql(client, BOOKING_ARRIVAL, {"id": str(booking.pk)}, token(poster(booking)))
    assert poster_view["data"]["booking"]["arrival"]["canTap"] is False
    assert poster_view["data"]["booking"]["arrival"]["canConfirmNoShow"] is False

    # Over an hour later with no arrival: the poster may say they didn't come.
    Booking.objects.filter(pk=booking.pk).update(slot_start=now - 2 * H)
    BookingArrival.objects.filter(booking=booking).update(eta_at=now - 2 * H)
    poster_view = gql(client, BOOKING_ARRIVAL, {"id": str(booking.pk)}, token(poster(booking)))
    shown = poster_view["data"]["booking"]["arrival"]
    assert shown["canConfirmNoShow"] is True
    assert (shown["noShowRefundCents"], shown["noShowCreditCents"]) == (
        booking.amount_cents + booking.fee_cents,
        500,
    )
    done = gql(client, NO_SHOW, {"id": str(booking.pk)}, token(poster(booking)))
    result = done["data"]["confirmHelperArrival"]
    assert result["status"] == "CANCEL_REQUESTED"
    assert result["arrival"]["noShowContestBy"] is not None

    helper_view = gql(client, BOOKING_ARRIVAL, {"id": str(booking.pk)}, token(helper(booking)))
    assert helper_view["data"]["booking"]["arrival"]["canContestNoShow"] is True
    contested = gql(client, CONTEST, {"id": str(booking.pk)}, token(helper(booking)))
    assert contested["data"]["contestNoShow"]["arrival"]["noShowAwaitingSupport"] is True


def test_the_step_names_match_the_push_table() -> None:
    from bookings.outbox_handlers import ARRIVAL_PUSHES
    from notifications.kinds import KINDS

    assert set(ARRIVAL_PUSHES) == set(Step) - {Step.CAME_CONFIRMED}
    for pushes in ARRIVAL_PUSHES.values():
        for kind, _side in pushes:
            assert kind in KINDS


def test_running_late_cannot_reopen_a_free_cancel_after_support_rejected_a_no_show(
    client: Client, gateway: FakeGateway
) -> None:
    booking, said_at = reported(client)
    arrival.contest_no_show(helper(booking), booking.pk, now=said_at + H)
    arrival.decide_no_show(make_staff(Role.SUPPORT), booking.pk, uphold=False, now=said_at + 2 * H)
    BookingArrival.objects.filter(booking=booking).update(arrived_at=None)
    booking.refresh_from_db()

    with pytest.raises(ApiError) as refused:
        arrival.running_late(helper(booking), booking.pk, 30, now=said_at + 3 * H)
    assert code(refused) == ErrorCode.BOOKING_ACTION_NOT_ALLOWED.value


def test_running_late_cannot_reopen_a_free_cancel_after_support_rejected_a_no_fault_request(
    client: Client, gateway: FakeGateway
) -> None:
    booking, start = booked(client)
    arrival.running_late(helper(booking), booking.pk, 60, now=start - 10 * M)
    NoFaultRequest.objects.create(
        booking=booking,
        reason=NoFaultReason.WEATHER,
        requested_by=ActorKind.POSTER,
        requested_at=start - 2 * H,
        respond_by=start + 46 * H,
        decision=NoFaultRequest.Decision.REJECTED,
        decided_at=start - H,
        closed_at=start - H,
    )
    # The earlier report's free cancel is closed too, and a new one is refused.
    assert not arrival.can_answer_late(booking, arrival_of(booking), start)
    with pytest.raises(ApiError) as refused:
        arrival.running_late(helper(booking), booking.pk, 30, now=start + 5 * M)
    assert code(refused) == ErrorCode.BOOKING_ACTION_NOT_ALLOWED.value


def test_the_poster_is_told_when_an_uncontested_no_show_settles(
    client: Client, gateway: FakeGateway
) -> None:
    booking, said_at = reported(client)
    arrival.settle_uncontested_no_shows(said_at + D)
    publish(arrival.ARRIVAL_TOPIC, booking)
    assert "booking.no_show_refund" in kinds_for(booking.poster_id)
    assert "booking.no_show_confirmed" in kinds_for(booking.helper_id)


def test_cant_come_through_the_api_is_the_helper_cancellation_with_the_credit(
    client: Client, gateway: FakeGateway
) -> None:
    from bookings.tests.test_helper_cancel import CANCEL_AS_HELPER

    now = timezone.now()
    booking = paid(client)
    Booking.objects.filter(pk=booking.pk).update(created_at=now - 30 * H, slot_start=now - 20 * M)
    body = gql(
        client,
        CANCEL_AS_HELPER,
        {"id": str(booking.pk), "reason": "CANT_COME"},
        token(helper(booking)),
    )
    done = body["data"]["cancelAsHelper"]["cancellation"]
    assert (done["case"], done["requestedBy"], done["posterCreditCents"]) == (
        "HELPER_CANCEL",
        "HELPER",
        500,
    )


def test_the_early_start_event_records_what_the_poster_was_shown(
    client: Client, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    booking, start = booked(client)
    # A trader helper inside the 14 days (consent records are append-only; the period
    # itself is tested in test_withdrawal_amounts).
    monkeypatch.setattr(cancel, "withdrawal_available", lambda booking, now=None: True)
    who = helper(booking)
    arrival.arrived(who, booking.pk, now=start - 45 * M)
    arrival.request_early_start(who, booking.pk, now=start - 40 * M)
    # The copy keeps the withdrawal right (rule g).
    assert (
        arrival.early_start_wording(booking, start - 39 * M) == "arrival.earlyStartBodyWithdrawal"
    )

    arrival.respond_to_early_start(
        poster(booking), booking.pk, confirm=True, locale="de", now=start - 38 * M
    )

    data = BookingEvent.objects.get(booking=booking, type="early_start_confirmed").data
    assert data["shown"] == "arrival.earlyStartBodyWithdrawal"
    assert data["locale"] == "de"
    assert data["cancellation_version"] == booking.cancellation_version


# --- Limits from the security review. ---


def test_the_free_cancel_closes_at_the_new_eta(client: Client, gateway: FakeGateway) -> None:
    booking, start = booked(client)
    arrival.running_late(helper(booking), booking.pk, 25, now=start + 15 * M)

    with pytest.raises(ApiError) as closed:
        # The ETA is start + 40; the 30-minute minimum after the report runs to start + 45.
        arrival.respond_to_late(poster(booking), booking.pk, keep=False, now=start + 45 * M)

    assert code(closed) == ErrorCode.BOOKING_ACTION_NOT_ALLOWED.value
    assert not CancellationSettlement.objects.filter(booking=booking).exists()


def test_on_my_way_is_tapped_once(client: Client, gateway: FakeGateway) -> None:
    booking, start = booked(client)
    arrival.on_my_way(helper(booking), booking.pk, 10, now=start - 20 * M)

    with pytest.raises(ApiError) as again:
        arrival.on_my_way(helper(booking), booking.pk, 10, now=start - 10 * M)

    assert code(again) == ErrorCode.BOOKING_ACTION_NOT_ALLOWED.value


def test_late_reports_cannot_push_the_no_show_question_past_the_cap(
    client: Client, gateway: FakeGateway
) -> None:
    booking, start = booked(client)
    for minutes_after in (15, 200, 400):
        arrival.running_late(helper(booking), booking.pk, 240, now=start + minutes_after * M)

    due = arrival.no_show_due(booking, arrival_of(booking))

    assert due == start + arrival.NO_SHOW_DUE_LATEST


def test_a_short_late_eta_still_leaves_the_poster_30_minutes(
    client: Client, gateway: FakeGateway
) -> None:
    booking, start = booked(client)
    arrival.running_late(helper(booking), booking.pk, 2, now=start)

    assert arrival.can_answer_late(booking, arrival_of(booking), start + 29 * M)
    assert not arrival.can_answer_late(booking, arrival_of(booking), start + 30 * M)


def test_on_my_way_cannot_overwrite_a_late_report(client: Client, gateway: FakeGateway) -> None:
    booking, start = booked(client)
    arrival.running_late(helper(booking), booking.pk, 30, now=start + 15 * M)

    with pytest.raises(ApiError) as refused:
        arrival.on_my_way(helper(booking), booking.pk, 1, now=start + 16 * M)

    assert code(refused) == ErrorCode.BOOKING_ACTION_NOT_ALLOWED.value
    assert arrival_of(booking).eta_at == start + 45 * M


def test_a_second_late_report_cannot_shorten_the_answer_window(
    client: Client, gateway: FakeGateway
) -> None:
    booking, start = booked(client)
    arrival.running_late(helper(booking), booking.pk, 120, now=start + 15 * M)
    arrival.running_late(helper(booking), booking.pk, 5, now=start + 16 * M)

    record = arrival_of(booking)
    assert record.late_answer_until == start + 135 * M
    assert arrival.can_answer_late(booking, record, start + 100 * M)
