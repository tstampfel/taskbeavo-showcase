"""The booking state machine: every legal move, every illegal one, who may make it, the
webhook-only money states (hard rule a), the audit and outbox rows (hard rule c), and
two racing callers."""

import itertools
import threading
import time
from typing import Any

import pytest
from django.db import connection, transaction
from django.utils import timezone

from accounts.models import User
from accounts.tests.factories import make_user
from areas.models import ServiceArea
from bookings import queries, state_machine
from bookings.models import Booking, BookingEvent
from bookings.state_machine import MONEY_CONFIRMED, TRANSITIONS, apply_stripe_transition
from bookings.tests.factories import book, make_booking, stripe_event
from core import outbox
from core.errors import ApiError, ErrorCode
from core.models import ActorKind, Outbox
from offers import services as offers
from offers.tests.factories import make_helper, open_task
from payments.models import StripeEvent
from tasks.tests.factories import make_poster, ready_photo

pytestmark = pytest.mark.django_db

S = Booking.Status
A = ActorKind
CLIENT_ACTORS = (A.POSTER, A.HELPER, A.ADMIN, A.SYSTEM)


def actor_for(booking: Booking, kind: A) -> User | None:
    if kind == A.POSTER:
        return booking.poster
    if kind == A.HELPER:
        return booking.helper
    if kind == A.ADMIN:
        return User.objects.filter(is_staff=True).first() or make_user("staff", is_staff=True)
    return None


def events(booking: Booking) -> list[BookingEvent]:
    return list(BookingEvent.objects.filter(booking=booking).order_by("id"))


def updates(booking: Booking) -> list[Outbox]:
    return list(
        Outbox.objects.filter(
            topic=state_machine.UPDATED_TOPIC, aggregate_id=str(booking.pk)
        ).order_by("id")
    )


def error_code(exc: pytest.ExceptionInfo[ApiError]) -> str:
    return exc.value.extensions["code"]


# Creation.


def test_creating_a_booking_writes_its_event_and_outbox_row() -> None:
    booking = make_booking(price_cents=3_000)

    assert booking.status == S.AWAITING_PAYMENT
    assert booking.amount_cents == 3_000
    assert booking.fee_cents == 360
    assert booking.helper_id == booking.offer.helper_id
    assert booking.poster_id == booking.task.poster_id
    assert booking.slot_start == booking.task.window_start
    [event] = events(booking)
    assert (event.type, event.actor_kind, event.actor_id) == (
        "awaiting_payment",
        A.POSTER,
        booking.poster_id,
    )
    assert event.data == {"from": None, "to": "AWAITING_PAYMENT", "offer_id": str(booking.offer_id)}
    [row] = updates(booking)
    assert row.payload == {"booking_id": str(booking.pk), "status": "AWAITING_PAYMENT"}


@pytest.mark.parametrize(
    ("amount", "fee"),
    [
        (1_000, 250),  # 120 → the €2.50 minimum
        (2_079, 250),  # 249.48 → 249 → 250
        (2_083, 250),  # 249.96 → 250
        (2_088, 251),  # 250.56 → 251
        (3_000, 360),
        (3_004, 360),  # 360.48 → 360
        (3_005, 361),  # 360.60 → 361
        (20_000, 2_400),
    ],
)
def test_the_service_fee_is_12_percent_rounded_half_up_at_least_2_50(amount: int, fee: int) -> None:
    assert state_machine.service_fee_cents(amount) == fee


def test_only_the_tasks_poster_can_book_an_offer() -> None:
    task = open_task()
    offer = offers.send_offer(make_helper(), str(task.pk), 3_000)

    with pytest.raises(ApiError) as exc:
        book(make_user("stranger"), offer)

    assert error_code(exc) == ErrorCode.OFFER_NOT_FOUND.name
    assert not Booking.objects.exists()


def test_a_withdrawn_offer_cant_be_booked() -> None:
    task = open_task()
    helper = make_helper()
    offer = offers.send_offer(helper, str(task.pk), 3_000)
    offers.withdraw_offer(helper, str(offer.pk))

    with pytest.raises(ApiError) as exc:
        book(task.poster, offer)

    assert error_code(exc) == ErrorCode.OFFER_NOT_EDITABLE.name


def test_a_task_has_one_booking_in_progress_until_checkout_fails() -> None:
    task = open_task()
    first = offers.send_offer(make_helper(), str(task.pk), 3_000)
    other = offers.send_offer(make_helper("ivan"), str(task.pk), 2_500)
    booking = book(task.poster, first)

    with pytest.raises(ApiError) as exc:
        book(booking.poster, other)
    assert error_code(exc) == ErrorCode.TASK_ALREADY_BOOKED.name

    state_machine.transition(booking.pk, S.PAYMENT_FAILED, actor_kind=A.SYSTEM)
    assert book(booking.poster, other).status == S.AWAITING_PAYMENT


def test_booking_rechecks_the_service_area() -> None:
    """Hard rule j: an area switched off after the task was posted takes no bookings."""
    task = open_task()
    offer = offers.send_offer(make_helper(), str(task.pk), 3_000)
    ServiceArea.objects.filter(pk=task.service_area_id).update(active=False)

    with pytest.raises(ApiError) as exc:
        book(task.poster, offer)

    assert error_code(exc) == ErrorCode.OUT_OF_SERVICE_AREA.name
    assert not Booking.objects.exists()


def test_a_flexible_task_needs_the_agreed_day() -> None:
    task = open_task(window_start=None, window_end=None, flexible_date=True)
    offer = offers.send_offer(make_helper(), str(task.pk), 3_000)

    with pytest.raises(ApiError) as exc:
        book(task.poster, offer)
    assert error_code(exc) == ErrorCode.INVALID_TASK_DATES.name

    day = timezone.now() + timezone.timedelta(days=2)
    assert book(task.poster, offer, slot_start=day).slot_start == day


def test_a_fixed_date_task_keeps_its_date() -> None:
    task = open_task()
    offer = offers.send_offer(make_helper(), str(task.pk), 3_000)

    with pytest.raises(ApiError) as exc:
        book(task.poster, offer, slot_start=timezone.now() + timezone.timedelta(days=50))

    assert error_code(exc) == ErrorCode.INVALID_TASK_DATES.name


@pytest.mark.parametrize("days", [-1, 0, 31])
def test_a_flexible_tasks_day_lies_within_its_open_period(days: int) -> None:
    task = open_task(window_start=None, window_end=None, flexible_date=True)
    offer = offers.send_offer(make_helper(), str(task.pk), 3_000)

    with pytest.raises(ApiError) as exc:
        book(task.poster, offer, slot_start=timezone.now() + timezone.timedelta(days=days))

    assert error_code(exc) == ErrorCode.INVALID_TASK_DATES.name


def test_the_same_offer_can_be_booked_again_after_checkout_failed() -> None:
    booking = make_booking()
    state_machine.transition(booking.pk, S.PAYMENT_FAILED, actor_kind=A.SYSTEM)

    again = book(booking.poster, booking.offer)

    assert again.pk != booking.pk
    assert again.status == S.AWAITING_PAYMENT
    assert again.offer_id == booking.offer_id


@pytest.mark.parametrize("status", [s for s in S if s != S.PAYMENT_FAILED])
def test_a_booked_task_takes_no_offers(status: S) -> None:
    booking = make_booking(status)
    other = make_helper("ivan")

    with pytest.raises(ApiError) as exc:
        offers.send_offer(other, str(booking.task_id), 2_500)
    assert error_code(exc) == ErrorCode.TASK_NOT_OPEN_FOR_OFFERS.name
    with pytest.raises(ApiError) as exc:
        offers.renew_offer(booking.helper, str(booking.offer_id))
    assert error_code(exc) == ErrorCode.TASK_NOT_OPEN_FOR_OFFERS.name
    # The accepted offer is no longer live, so it can't be changed either.
    with pytest.raises(ApiError) as exc:
        offers.update_offer(booking.helper, str(booking.offer_id))
    assert error_code(exc) == ErrorCode.OFFER_NOT_EDITABLE.name


def test_a_failed_checkout_opens_the_task_to_offers_again() -> None:
    booking = make_booking()
    state_machine.transition(booking.pk, S.PAYMENT_FAILED, actor_kind=A.SYSTEM)

    offer = offers.send_offer(make_helper("ivan"), str(booking.task_id), 2_500)

    assert offer.is_live


# Every legal move.

CLIENT_MOVES = [
    (source, target, kind)
    for (source, target), kinds in TRANSITIONS.items()
    for kind in sorted(kinds)
    if kind != A.STRIPE_WEBHOOK
]
STRIPE_MOVES = [
    (source, target) for (source, target), kinds in TRANSITIONS.items() if A.STRIPE_WEBHOOK in kinds
]


@pytest.mark.parametrize(("source", "target", "kind"), CLIENT_MOVES)
def test_every_client_move_in_the_table_works(source: S, target: S, kind: A) -> None:
    booking = make_booking(source)
    if source == S.CANCEL_REQUESTED:
        # The requester withdraws the request; the other side disputes it.
        other = {A.POSTER: A.HELPER, A.HELPER: A.POSTER}.get(kind, A.POSTER)
        requester = kind if target == S.FUNDS_HELD else other
        Booking.objects.filter(pk=booking.pk).update(cancel_requested_by=requester)

    moved = state_machine.transition(
        booking.pk, target, actor_kind=kind, actor=actor_for(booking, kind)
    )

    assert moved.status == target
    event = events(booking)[-1]
    assert (event.type, event.actor_kind) == (target.lower(), kind)
    assert event.data == {"from": source.value, "to": target.value}
    assert updates(booking)[-1].payload == {"booking_id": str(booking.pk), "status": target.value}


@pytest.mark.parametrize(("source", "target"), STRIPE_MOVES)
def test_every_stripe_move_in_the_table_works(source: S, target: S) -> None:
    booking = make_booking(source)

    with transaction.atomic():
        event_id = stripe_event()
        moved = apply_stripe_transition(booking.pk, target, stripe_event_id=event_id)

    assert moved is not None and moved.status == target
    event = events(booking)[-1]
    assert (event.type, event.actor_kind, event.actor_id) == (
        target.lower(),
        A.STRIPE_WEBHOOK,
        None,
    )
    assert event.data == {"from": source.value, "to": target.value, "stripe_event": event_id}


# Every illegal move.

ILLEGAL_CLIENT_MOVES = [
    (source, target, kind)
    for source, target in itertools.product(S, S)
    for kind in CLIENT_ACTORS
    if kind not in TRANSITIONS.get((source, target), frozenset())
]


@pytest.mark.parametrize(("source", "target", "kind"), ILLEGAL_CLIENT_MOVES)
def test_every_other_client_move_is_refused(source: S, target: S, kind: A) -> None:
    booking = make_booking(source)
    before = (len(events(booking)), len(updates(booking)))

    with pytest.raises(ApiError) as exc:
        state_machine.transition(
            booking.pk, target, actor_kind=kind, actor=actor_for(booking, kind)
        )

    assert error_code(exc) == ErrorCode.BOOKING_ACTION_NOT_ALLOWED.name
    booking.refresh_from_db()
    assert booking.status == source
    assert (len(events(booking)), len(updates(booking))) == before


@pytest.mark.parametrize("target", sorted(MONEY_CONFIRMED))
@pytest.mark.parametrize("kind", CLIENT_ACTORS)
def test_no_client_confirms_money(target: S, kind: A) -> None:
    """Hard rule a: FUNDS_HELD, REFUNDED, PARTIALLY_SETTLED and PAID_OUT come from Stripe.
    Going back to FUNDS_HELD from a status where the money is already held (a reschedule, a
    withdrawn cancel request) confirms nothing new."""
    already_held = {S.CANCEL_REQUESTED, S.NO_ACCESS_REPORTED}
    sources = [
        s for (s, t) in TRANSITIONS if t == target and not (t == S.FUNDS_HELD and s in already_held)
    ]
    assert sources
    for source in sources:
        assert not state_machine.is_allowed(source, target, kind)


def test_the_stripe_actor_cant_be_claimed_through_transition() -> None:
    booking = make_booking()

    with pytest.raises(ValueError, match="apply_stripe_transition"):
        state_machine.transition(booking.pk, S.FUNDS_HELD, actor_kind=A.STRIPE_WEBHOOK)

    booking.refresh_from_db()
    assert booking.status == S.AWAITING_PAYMENT


def test_money_needs_a_recorded_stripe_event() -> None:
    booking = make_booking()

    with transaction.atomic(), pytest.raises(PermissionError):
        apply_stripe_transition(booking.pk, S.FUNDS_HELD, stripe_event_id="evt_forged")
    with transaction.atomic():
        event_id = stripe_event()
        StripeEvent.objects.filter(pk=event_id).update(processed_at=timezone.now())
        with pytest.raises(PermissionError):
            apply_stripe_transition(booking.pk, S.FUNDS_HELD, stripe_event_id=event_id)

    booking.refresh_from_db()
    assert booking.status == S.AWAITING_PAYMENT
    assert len(events(booking)) == 1


@pytest.mark.django_db(transaction=True)
def test_money_moves_only_inside_the_webhook_transaction() -> None:
    booking = make_booking()

    with pytest.raises(RuntimeError):
        apply_stripe_transition(booking.pk, S.FUNDS_HELD, stripe_event_id="evt_any")


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.FUNDS_HELD, S.FUNDS_HELD),
        (S.PAYMENT_REQUESTED, S.FUNDS_HELD),
        (S.CANCEL_REQUESTED, S.FUNDS_HELD),
        (S.DISPUTED, S.FUNDS_HELD),
        (S.PAID_OUT, S.PAID_OUT),
        (S.CLOSED, S.PAID_OUT),
        (S.CLOSED, S.REFUNDED),
        (S.DISPUTED, S.DISPUTED),
    ],
)
def test_a_late_or_replayed_stripe_event_changes_nothing(current: S, target: S) -> None:
    booking = make_booking(current)
    before = len(events(booking))

    with transaction.atomic():
        assert apply_stripe_transition(booking.pk, target, stripe_event_id=stripe_event()) is None

    booking.refresh_from_db()
    assert booking.status == current
    assert len(events(booking)) == before


@pytest.mark.parametrize(
    ("current", "target"),
    [
        # The payment went through after checkout was given up: a human refunds it.
        (S.PAYMENT_FAILED, S.FUNDS_HELD),
        # A chargeback after the payout, or a refund of a released payment.
        (S.PAID_OUT, S.DISPUTED),
        (S.CLOSED, S.DISPUTED),
        (S.RELEASED, S.REFUNDED),
        (S.AWAITING_PAYMENT, S.REFUNDED),
    ],
)
def test_a_stripe_event_that_doesnt_fit_is_flagged_for_support(
    current: S,
    target: S,
    caplog: pytest.LogCaptureFixture,
    django_capture_on_commit_callbacks: Any,
) -> None:
    """BEAV-110: the booking is left alone and support is told, so the webhook answers 200
    instead of failing on every one of Stripe's retries."""
    booking = make_booking(current)
    event_id = stripe_event()

    with django_capture_on_commit_callbacks(execute=True), transaction.atomic():
        assert apply_stripe_transition(booking.pk, target, stripe_event_id=event_id) is None

    booking.refresh_from_db()
    assert booking.status == current
    [flag] = BookingEvent.objects.filter(booking=booking, type=state_machine.SUPPORT_FLAG)
    assert flag.data == {
        "reason": "stripe_transition_refused",
        "from": current.value,
        "to": target.value,
        "stripe_event": event_id,
    }
    [alert] = [r for r in caplog.records if r.levelname == "ERROR"]
    assert getattr(alert, "booking.id") == str(booking.pk)


# Who may act.


@pytest.mark.parametrize("kind", [A.POSTER, A.HELPER])
def test_other_users_cant_act_as_poster_or_helper(kind: A) -> None:
    booking = make_booking(S.FUNDS_HELD)

    with pytest.raises(ApiError) as exc:
        state_machine.transition(
            booking.pk, S.INCIDENT_PAUSED, actor_kind=kind, actor=make_user("mallory")
        )

    assert error_code(exc) == ErrorCode.BOOKING_NOT_FOUND.name
    booking.refresh_from_db()
    assert booking.status == S.FUNDS_HELD


def test_a_deactivated_participant_cant_act() -> None:
    booking = make_booking(S.PAYMENT_REQUESTED)
    User.objects.filter(pk=booking.poster_id).update(is_active=False)
    booking.poster.refresh_from_db()

    with pytest.raises(ApiError) as exc:
        state_machine.transition(booking.pk, S.RELEASED, actor_kind=A.POSTER, actor=booking.poster)

    assert error_code(exc) == ErrorCode.UNAUTHENTICATED.name


def test_the_helper_isnt_the_poster() -> None:
    booking = make_booking(S.PAYMENT_REQUESTED)

    with pytest.raises(ApiError) as exc:
        state_machine.transition(booking.pk, S.RELEASED, actor_kind=A.POSTER, actor=booking.helper)

    assert error_code(exc) == ErrorCode.BOOKING_NOT_FOUND.name


def test_only_staff_act_as_admin() -> None:
    booking = make_booking(S.DISPUTED)

    with pytest.raises(ApiError) as exc:
        state_machine.transition(
            booking.pk, S.RELEASED, actor_kind=A.ADMIN, actor=make_user("notstaff")
        )

    assert error_code(exc) == ErrorCode.BOOKING_ACTION_NOT_ALLOWED.name


def test_the_system_acts_without_a_user() -> None:
    booking = make_booking()

    with pytest.raises(ApiError):
        state_machine.transition(
            booking.pk, S.PAYMENT_FAILED, actor_kind=A.SYSTEM, actor=booking.poster
        )


def test_an_unknown_or_malformed_booking_id_is_not_found() -> None:
    for booking_id in ("not-a-uuid", "00000000-0000-0000-0000-000000000000"):
        with pytest.raises(ApiError) as exc:
            state_machine.transition(booking_id, S.PAYMENT_FAILED, actor_kind=A.SYSTEM)
        assert error_code(exc) == ErrorCode.BOOKING_NOT_FOUND.name


def test_only_who_asked_to_cancel_can_withdraw_the_request() -> None:
    booking = make_booking(S.FUNDS_HELD)
    state_machine.transition(
        booking.pk, S.CANCEL_REQUESTED, actor_kind=A.HELPER, actor=booking.helper
    )

    with pytest.raises(ApiError) as exc:
        state_machine.transition(
            booking.pk, S.FUNDS_HELD, actor_kind=A.POSTER, actor=booking.poster
        )
    assert error_code(exc) == ErrorCode.BOOKING_ACTION_NOT_ALLOWED.name

    moved = state_machine.transition(
        booking.pk, S.FUNDS_HELD, actor_kind=A.HELPER, actor=booking.helper
    )
    assert moved.status == S.FUNDS_HELD
    assert (moved.cancel_requested_by, moved.cancel_requested_at) == ("", None)


# What each move records.


def test_the_happy_path_records_its_timestamps() -> None:
    booking = make_booking()
    with transaction.atomic():
        apply_stripe_transition(booking.pk, S.FUNDS_HELD, stripe_event_id=stripe_event())
    state_machine.transition(
        booking.pk, S.PAYMENT_REQUESTED, actor_kind=A.HELPER, actor=booking.helper
    )
    released = state_machine.transition(
        booking.pk, S.RELEASED, actor_kind=A.POSTER, actor=booking.poster
    )
    assert released.payment_requested_at is not None
    assert released.released_at is not None
    assert released.released_by == A.POSTER
    assert released.closed_at is None
    with transaction.atomic():
        apply_stripe_transition(booking.pk, S.PAID_OUT, stripe_event_id=stripe_event("payout.paid"))
    closed = state_machine.transition(booking.pk, S.CLOSED, actor_kind=A.SYSTEM)

    assert closed.closed_at is not None
    assert [e.type for e in events(booking)] == [
        "awaiting_payment",
        "funds_held",
        "payment_requested",
        "released",
        "paid_out",
        "closed",
    ]
    assert len(updates(booking)) == 6


def test_a_cancel_request_records_who_asked() -> None:
    booking = make_booking(S.FUNDS_HELD)

    moved = state_machine.transition(
        booking.pk, S.CANCEL_REQUESTED, actor_kind=A.POSTER, actor=booking.poster
    )

    assert moved.cancel_requested_by == A.POSTER
    assert moved.cancel_requested_at is not None


def test_a_failed_move_rolls_back_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    booking = make_booking(S.FUNDS_HELD)

    def broken(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("outbox down")

    monkeypatch.setattr(state_machine, "enqueue", broken)
    with pytest.raises(RuntimeError):
        state_machine.transition(
            booking.pk, S.PAYMENT_REQUESTED, actor_kind=A.HELPER, actor=booking.helper
        )

    booking.refresh_from_db()
    assert booking.status == S.FUNDS_HELD
    assert [e.type for e in events(booking)] == ["awaiting_payment"]


def test_event_data_holds_ids_only() -> None:
    booking = make_booking(S.FUNDS_HELD)

    with pytest.raises(ValueError):
        state_machine.transition(
            booking.pk,
            S.CANCEL_REQUESTED,
            actor_kind=A.POSTER,
            actor=booking.poster,
            data={"gate_code": "1234"},
        )

    booking.refresh_from_db()
    assert booking.status == S.FUNDS_HELD


def test_a_dispute_holds_the_task_photos_until_it_is_settled() -> None:
    poster = make_poster("ana")
    photo = ready_photo(poster)
    booking = make_booking(S.FUNDS_HELD, poster=poster, photo_ids=[photo.pk])

    state_machine.transition(booking.pk, S.DISPUTED, actor_kind=A.POSTER, actor=booking.poster)
    photo.refresh_from_db()
    assert photo.legal_hold

    state_machine.transition(
        booking.pk, S.RELEASED, actor_kind=A.ADMIN, actor=make_user("staff", is_staff=True)
    )
    photo.refresh_from_db()
    assert not photo.legal_hold
    assert photo.delete_after is not None


# Who may see it, and what is still open.


def test_active_means_paid_and_not_yet_settled() -> None:
    assert set(Booking.ACTIVE) == {
        S.FUNDS_HELD,
        S.CANCEL_REQUESTED,
        S.NO_ACCESS_REPORTED,
        S.INCIDENT_PAUSED,
        S.PAYMENT_REQUESTED,
    }
    for status in S:
        # A cancel requested is active only while a no-fault request is pending
        # (test_helper_cancel.py); this unsaved booking has none.
        expected = status in Booking.ACTIVE and status != S.CANCEL_REQUESTED
        assert Booking(status=status).is_active == expected


@pytest.mark.parametrize("status", list(S))
def test_open_money_bookings_block_account_deletion(status: S) -> None:
    booking = make_booking(status)
    expected = status in Booking.OPEN_MONEY

    assert queries.user_has_open_money_bookings(booking.poster) is expected
    assert queries.user_has_open_money_bookings(booking.helper) is expected
    assert not queries.user_has_open_money_bookings(make_user("bystander"))


def test_open_money_covers_every_status_before_the_money_settles() -> None:
    settled = {S.PAYMENT_FAILED, S.REFUNDED, S.PAID_OUT, S.CLOSED}
    assert set(Booking.OPEN_MONEY) == set(S) - settled


# The outbox row.


def test_the_update_reaches_the_poster_and_the_helper_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bookings import outbox_handlers

    sent: list[tuple[int, list[int]]] = []
    monkeypatch.setattr(
        outbox_handlers, "send_to_users", lambda row, ids: sent.append((row.pk, list(ids)))
    )
    booking = make_booking()
    [row] = updates(booking)

    outbox.publish_row(row.pk)

    row.refresh_from_db()
    assert row.status == Outbox.Status.PUBLISHED
    assert sent == [(row.pk, [booking.poster_id, booking.helper_id])]


def test_the_update_topic_has_a_handler() -> None:
    assert state_machine.UPDATED_TOPIC in outbox._handlers


# Concurrency.


@pytest.mark.django_db(transaction=True)
def test_two_release_taps_release_once() -> None:
    """Two "Release payment" taps at once: the second waits on the row lock, then sees
    RELEASED and is refused. One event, one outbox row: one payout later (BEAV-110)."""
    booking = make_booking(S.PAYMENT_REQUESTED)
    first_locked = threading.Event()
    results: list[str] = []

    original = state_machine._check_actor

    def slow_check(locked: Booking, *args: object) -> None:
        original(locked, *args)  # type: ignore[arg-type]
        if not first_locked.is_set():
            first_locked.set()
            time.sleep(0.3)

    def release() -> None:
        try:
            state_machine.transition(
                booking.pk, S.RELEASED, actor_kind=A.POSTER, actor=booking.poster
            )
            results.append("released")
        except ApiError as exc:
            results.append(exc.extensions["code"])
        finally:
            connection.close()

    state_machine._check_actor = slow_check  # type: ignore[assignment]
    try:
        first = threading.Thread(target=release)
        first.start()
        assert first_locked.wait(5)
        second = threading.Thread(target=release)
        second.start()
        first.join()
        second.join()
    finally:
        state_machine._check_actor = original  # type: ignore[assignment]

    assert sorted(results) == [ErrorCode.BOOKING_ACTION_NOT_ALLOWED.name, "released"]
    booking.refresh_from_db()
    assert booking.status == S.RELEASED
    assert [e.type for e in events(booking)] == ["awaiting_payment", "released"]
    assert [r.payload["status"] for r in updates(booking)] == ["AWAITING_PAYMENT", "RELEASED"]


@pytest.mark.django_db(transaction=True)
def test_a_webhook_racing_a_withdrawal_applies_once() -> None:
    """payment_intent.succeeded and the abandoned-checkout sweep at the same moment: exactly
    one of FUNDS_HELD and PAYMENT_FAILED wins, and the loser changes nothing."""
    booking = make_booking()
    results: list[str] = []
    barrier = threading.Barrier(2)

    def webhook() -> None:
        try:
            barrier.wait(5)
            with transaction.atomic():
                moved = apply_stripe_transition(
                    booking.pk, S.FUNDS_HELD, stripe_event_id=stripe_event()
                )
            results.append("funds_held" if moved else "ignored")
        finally:
            connection.close()

    def sweep() -> None:
        try:
            barrier.wait(5)
            state_machine.transition(booking.pk, S.PAYMENT_FAILED, actor_kind=A.SYSTEM)
            results.append("payment_failed")
        except ApiError:
            results.append("refused")
        finally:
            connection.close()

    threads = [threading.Thread(target=webhook), threading.Thread(target=sweep)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    booking.refresh_from_db()
    # Whoever locked first won; a payment confirmed after the sweep is flagged for support.
    assert sorted(results) in (["funds_held", "refused"], ["ignored", "payment_failed"])
    flags = BookingEvent.objects.filter(booking=booking, type=state_machine.SUPPORT_FLAG)
    assert flags.count() == (1 if "payment_failed" in results else 0)
    assert len(events(booking)) == 2 + flags.count()


@pytest.mark.django_db(transaction=True)
def test_two_offers_accepted_at_once_book_the_task_once() -> None:
    """Two devices accept two offers on one task: the task lock lets one through."""
    task = open_task()
    accepted = [
        offers.send_offer(make_helper(name), str(task.pk), 3_000) for name in ("marko", "ivan")
    ]
    results: list[str] = []
    barrier = threading.Barrier(2)

    def accept(offer: object) -> None:
        try:
            barrier.wait(5)
            book(task.poster, offer)  # type: ignore[arg-type]
            results.append("booked")
        except ApiError as exc:
            results.append(exc.extensions["code"])
        finally:
            connection.close()

    threads = [threading.Thread(target=accept, args=(offer,)) for offer in accepted]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == ["TASK_ALREADY_BOOKED", "booked"]
    assert Booking.objects.filter(task=task).count() == 1
