"""What accepting an offer does to the task's offers and to the task (review r1: R1-1,
R1-2, R1-4, R1-5, R1-6): the chosen offer ACCEPTED, the others DECLINED, both reopened
by a failed checkout, and a booked task that keeps its details."""

import datetime as dt
import threading

import pytest
from django.db import connection
from django.utils import timezone

from accounts.models import User
from bookings import state_machine
from bookings.models import Booking
from bookings.tests.factories import book, make_booking
from core import outbox
from core.errors import ApiError, ErrorCode
from core.models import ActorKind, Outbox
from offers import services as offers
from offers.models import Offer, OfferEvent
from offers.tests.factories import make_helper, open_task
from tasks import services as tasks
from tasks.models import Task
from tasks.tests.factories import changes, make_poster, ready_photo, window

pytestmark = pytest.mark.django_db

S = Booking.Status
A = ActorKind


def code(exc: pytest.ExceptionInfo[ApiError]) -> str:
    return exc.value.extensions["code"]


def offer_events(offer: Offer) -> list[str]:
    return list(
        OfferEvent.objects.filter(offer=offer).order_by("id").values_list("type", flat=True)
    )


def rows(topic: str, offer: Offer) -> list[Outbox]:
    return list(Outbox.objects.filter(topic=topic, aggregate_id=str(offer.pk)).order_by("id"))


def three_offers() -> tuple[Task, Offer, Offer, Offer]:
    """A task with three live offers: the one the poster accepts and two others."""
    task = open_task()
    chosen, second, third = (
        offers.send_offer(make_helper(name), str(task.pk), price)
        for name, price in (("marko", 3_000), ("ivan", 2_800), ("luka", 3_200))
    )
    return task, chosen, second, third


def fail(booking: Booking) -> Booking:
    return state_machine.transition(booking.pk, S.PAYMENT_FAILED, actor_kind=A.SYSTEM)


# Accepting and declining.


def test_accepting_one_offer_declines_the_others() -> None:
    task, chosen, second, third = three_offers()
    withdrawn = offers.send_offer(make_helper("ante"), str(task.pk), 2_000)
    offers.withdraw_offer(withdrawn.helper, str(withdrawn.pk))

    booking = book(task.poster, chosen)

    for offer in (chosen, second, third, withdrawn):
        offer.refresh_from_db()
    assert chosen.status == Offer.Status.ACCEPTED
    assert chosen.closed_at is None
    assert (second.status, third.status) == (Offer.Status.DECLINED, Offer.Status.DECLINED)
    assert second.closed_at is not None
    assert withdrawn.status == Offer.Status.WITHDRAWN
    assert sorted(booking.declined_offer_ids) == sorted([second.pk, third.pk])
    assert offer_events(chosen)[-1] == "accepted"
    assert offer_events(second)[-1] == "declined"
    [row] = rows(state_machine.OFFER_DECLINED_TOPIC, second)
    assert row.payload == {
        "offer_id": str(second.pk),
        "task_id": str(task.pk),
        "booking_id": str(booking.pk),
    }
    assert rows(state_machine.OFFER_DECLINED_TOPIC, chosen) == []
    assert rows(state_machine.OFFER_DECLINED_TOPIC, withdrawn) == []


def test_the_accepted_offer_cant_be_withdrawn_expire_or_need_an_update() -> None:
    booking = make_booking(S.FUNDS_HELD)
    offer = booking.offer
    past = timezone.now() - dt.timedelta(minutes=1)
    Offer.objects.filter(pk=offer.pk).update(expires_at=past)

    with pytest.raises(ApiError) as exc:
        offers.withdraw_offer(booking.helper, str(offer.pk))
    assert code(exc) == ErrorCode.OFFER_NOT_EDITABLE.name
    assert offers.expire_offers() == 0
    assert offers.queue_expiry_reminders() == 0
    task = Task.objects.get(pk=booking.task_id)
    assert offers.mark_needs_update(task, booking.poster) == 0

    offer.refresh_from_db()
    assert offer.status == Offer.Status.ACCEPTED
    assert not offer.task_changed


def test_the_declined_helper_hears_live() -> None:
    from bookings import outbox_handlers

    task, chosen, second, _ = three_offers()
    sent: list[list[int]] = []
    original = outbox_handlers.send_to_users
    outbox_handlers.send_to_users = lambda row, ids: sent.append(list(ids))  # type: ignore[assignment]
    try:
        book(task.poster, chosen)
        outbox.publish_row(rows(state_machine.OFFER_DECLINED_TOPIC, second)[0].pk)
    finally:
        outbox_handlers.send_to_users = original  # type: ignore[assignment]

    assert [second.helper_id] in sent


def test_the_topics_have_handlers() -> None:
    assert state_machine.OFFER_DECLINED_TOPIC in outbox._handlers
    assert state_machine.OFFER_REOPENED_TOPIC in outbox._handlers


# A failed or abandoned checkout.


def test_a_failed_checkout_reopens_the_offers() -> None:
    task, chosen, second, third = three_offers()
    booking = book(task.poster, chosen)
    # One declined offer's expiry passes during checkout; another was marked Needs update
    # before the poster accepted.
    Offer.objects.filter(pk=second.pk).update(expires_at=timezone.now() - dt.timedelta(seconds=1))
    Offer.objects.filter(pk=third.pk).update(task_changed=True)

    fail(booking)

    for offer in (chosen, second, third):
        offer.refresh_from_db()
    assert chosen.status == Offer.Status.SENT
    assert second.status == Offer.Status.EXPIRED
    assert second.closed_at is not None
    assert third.status == Offer.Status.NEEDS_UPDATE
    assert third.closed_at is None
    for offer in (chosen, second, third):
        assert offer_events(offer)[-1] == "reopened"
        [row] = rows(state_machine.OFFER_REOPENED_TOPIC, offer)
        assert row.payload["booking_id"] == str(booking.pk)


def test_the_poster_can_accept_again_after_a_failed_checkout() -> None:
    task, chosen, second, _ = three_offers()
    fail(book(task.poster, chosen))

    again = book(task.poster, second)

    chosen.refresh_from_db()
    second.refresh_from_db()
    assert again.offer_id == second.pk
    assert (second.status, chosen.status) == (Offer.Status.ACCEPTED, Offer.Status.DECLINED)
    assert chosen.pk in again.declined_offer_ids


def test_only_the_offers_the_booking_declined_reopen() -> None:
    task, chosen, second, _ = three_offers()
    booking = book(task.poster, chosen)
    # Declined by an earlier booking that was settled some other way: not ours to reopen.
    Booking.objects.filter(pk=booking.pk).update(declined_offer_ids=[])

    fail(booking)

    second.refresh_from_db()
    assert second.status == Offer.Status.DECLINED


@pytest.mark.parametrize("status", [s for s in S if s not in (S.AWAITING_PAYMENT,)])
def test_only_a_failed_checkout_reopens(status: S) -> None:
    task, chosen, second, _ = three_offers()
    booking = book(task.poster, chosen)
    Booking.objects.filter(pk=booking.pk).update(status=status)

    second.refresh_from_db()
    assert second.status == Offer.Status.DECLINED


# What the poster may book.


def test_an_offer_waiting_for_the_helpers_confirmation_cant_be_booked() -> None:
    task = open_task()
    offer = offers.send_offer(make_helper(), str(task.pk), 3_000)
    Offer.objects.filter(pk=offer.pk).update(status=Offer.Status.NEEDS_UPDATE, task_changed=True)
    offer.refresh_from_db()

    with pytest.raises(ApiError) as exc:
        book(task.poster, offer)

    assert code(exc) == ErrorCode.OFFER_NEEDS_UPDATE.name
    assert not Booking.objects.exists()


def test_the_price_must_be_the_one_the_poster_saw() -> None:
    task = open_task()
    helper = make_helper()
    offer = offers.send_offer(helper, str(task.pk), 3_000)
    offers.update_offer(helper, str(offer.pk), price_cents=3_500)

    with pytest.raises(ApiError) as exc:
        state_machine.create_booking(
            task.poster,
            offer,
            expected_price_cents=3_000,
            expected_total_cents=3_360,
            expected_fee_schedule_version="1",
        )

    assert code(exc) == ErrorCode.OFFER_CHANGED.name
    assert not Booking.objects.exists()
    booking = state_machine.create_booking(
        task.poster,
        offer,
        expected_price_cents=3_500,
        expected_total_cents=3_920,
        expected_fee_schedule_version="1",
    )
    assert booking.amount_cents == 3_500


def test_a_naive_day_is_refused() -> None:
    task = open_task(window_start=None, window_end=None, flexible_date=True)
    offer = offers.send_offer(make_helper(), str(task.pk), 3_000)
    naive = dt.datetime.now() + dt.timedelta(days=2)

    with pytest.raises(ApiError) as exc:
        book(task.poster, offer, slot_start=naive)

    assert code(exc) == ErrorCode.INVALID_TASK_DATES.name


# Cancel requests.


@pytest.mark.parametrize(("requester", "other"), [(A.POSTER, A.HELPER), (A.HELPER, A.POSTER)])
def test_only_the_other_side_disputes_a_cancel_request(requester: A, other: A) -> None:
    booking = make_booking(S.FUNDS_HELD)
    users = {A.POSTER: booking.poster, A.HELPER: booking.helper}
    state_machine.transition(
        booking.pk, S.CANCEL_REQUESTED, actor_kind=requester, actor=users[requester]
    )

    with pytest.raises(ApiError) as exc:
        state_machine.transition(
            booking.pk, S.DISPUTED, actor_kind=requester, actor=users[requester]
        )
    assert code(exc) == ErrorCode.BOOKING_ACTION_NOT_ALLOWED.name

    moved = state_machine.transition(booking.pk, S.DISPUTED, actor_kind=other, actor=users[other])
    assert moved.status == S.DISPUTED


# A booked task keeps its details (R1-1); access details: test_access_details.py.

START, END = window(days=5)
DETAIL_CHANGES = {
    "window": {"window_start": START, "window_end": END},
    "location": {"location": tasks.Location("Ulica Ante Starčevića 2, Dubrovnik", 42.661, 18.085)},
    "care_contact": {"care_contact_id": ""},
    "title": {"title": "Mow the whole lawn"},
    "description": {"description": "Mow the lawn, trim the hedge and rake the leaves."},
    "category": {"category": "CLEANING"},
    "budget": {"budget_cents": 4_000},
}


@pytest.mark.parametrize("field", list(DETAIL_CHANGES))
@pytest.mark.parametrize("status", [s for s in S if s != S.PAYMENT_FAILED])
def test_a_booked_task_keeps_its_details(status: S, field: str) -> None:
    booking = make_booking(status)
    before = Task.objects.get(pk=booking.task_id)

    with pytest.raises(ApiError) as exc:
        tasks.update_task(
            booking.poster, str(booking.task_id), tasks.TaskChanges(**DETAIL_CHANGES[field])
        )

    assert code(exc) == ErrorCode.TASK_BOOKED.name
    after = Task.objects.get(pk=booking.task_id)
    assert (after.window_start, after.address, after.access_details, after.updated_at) == (
        before.window_start,
        before.address,
        before.access_details,
        before.updated_at,
    )
    offer = Offer.objects.get(pk=booking.offer_id)
    assert offer.status == Offer.Status.ACCEPTED


def test_a_booked_task_can_still_get_photos() -> None:
    poster = make_poster("ana")
    booking = make_booking(S.FUNDS_HELD, poster=poster)
    photo = ready_photo(poster)

    tasks.update_task(poster, str(booking.task_id), tasks.TaskChanges(photo_ids=[photo.pk]))

    assert list(Task.objects.get(pk=booking.task_id).photos.all()) == [photo]


def test_after_a_failed_checkout_the_task_can_change_again() -> None:
    booking = make_booking()
    fail(booking)

    tasks.update_task(
        booking.poster, str(booking.task_id), tasks.TaskChanges(access_details="Key next door")
    )

    assert Task.objects.get(pk=booking.task_id).access_details == "Key next door"


def test_an_unbooked_task_still_marks_its_offers() -> None:
    task = tasks.post_task(make_poster("ana"), changes()).task
    offer = offers.send_offer(make_helper(), str(task.pk), 3_000)

    tasks.update_task(
        task.poster, str(task.pk), tasks.TaskChanges(window_start=START, window_end=END)
    )

    offer.refresh_from_db()
    assert offer.status == Offer.Status.NEEDS_UPDATE


# Concurrency.


def race(*actions: object) -> list[str]:
    results: list[str] = []
    barrier = threading.Barrier(len(actions))

    def run(action: object) -> None:
        try:
            barrier.wait(5)
            action()  # type: ignore[operator]
            results.append("ok")
        except ApiError as exc:
            results.append(exc.extensions["code"])
        finally:
            connection.close()

    threads = [threading.Thread(target=run, args=(action,)) for action in actions]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


@pytest.mark.django_db(transaction=True)
def test_accepting_and_withdrawing_the_same_offer_at_once() -> None:
    """The poster accepts while the helper withdraws. The task lock orders them: either
    the withdrawal comes first and the offer can't be booked, or the booking comes first
    and the withdrawal ends its checkout (test_withdrawal.py). Never a live checkout for a
    withdrawn offer."""
    task = open_task()
    helper = make_helper()
    offer = offers.send_offer(helper, str(task.pk), 3_000)

    results = race(
        lambda: book(task.poster, offer),
        lambda: offers.withdraw_offer(helper, str(offer.pk)),
    )

    offer.refresh_from_db()
    assert offer.status == Offer.Status.WITHDRAWN
    booking = Booking.objects.filter(offer=offer).first()
    if booking is None:
        assert sorted(results) == sorted(["ok", ErrorCode.OFFER_NOT_EDITABLE.name])
    else:
        assert results == ["ok", "ok"]
        assert booking.status == S.PAYMENT_FAILED


@pytest.mark.django_db(transaction=True)
def test_accepting_while_another_offer_is_withdrawn() -> None:
    """Another helper withdraws during the acceptance: their offer ends WITHDRAWN or
    DECLINED, never both, and the booking reopens only what it declined."""
    task = open_task()
    chosen = offers.send_offer(make_helper("marko"), str(task.pk), 3_000)
    leaving_helper = make_helper("ivan")
    leaving = offers.send_offer(leaving_helper, str(task.pk), 2_800)

    results = race(
        lambda: book(task.poster, chosen),
        lambda: offers.withdraw_offer(leaving_helper, str(leaving.pk)),
    )

    assert "ok" in results
    leaving.refresh_from_db()
    booking = Booking.objects.get(task=task)
    if leaving.status == Offer.Status.DECLINED:
        assert results.count("ok") == 1
        assert booking.declined_offer_ids == [leaving.pk]
    else:
        assert results == ["ok", "ok"]
        assert leaving.status == Offer.Status.WITHDRAWN
        assert booking.declined_offer_ids == []
    fail(booking)
    leaving.refresh_from_db()
    assert leaving.status in (Offer.Status.SENT, Offer.Status.WITHDRAWN)


@pytest.mark.django_db(transaction=True)
def test_editing_while_the_poster_accepts() -> None:
    """The poster's other device edits the date while this one accepts: the task lock
    orders them, so either the offer is marked Needs update first and can't be booked, or
    the booking wins and the edit is refused."""
    task = open_task()
    offer = offers.send_offer(make_helper(), str(task.pk), 3_000)
    poster = User.objects.get(pk=task.poster_id)

    results = race(
        lambda: book(poster, offer),
        lambda: tasks.update_task(
            poster, str(task.pk), tasks.TaskChanges(window_start=START, window_end=END)
        ),
    )

    offer.refresh_from_db()
    if Booking.objects.filter(task=task).exists():
        assert sorted(results) == sorted(["ok", ErrorCode.TASK_BOOKED.name])
        assert offer.status == Offer.Status.ACCEPTED
    else:
        assert sorted(results) == sorted(["ok", ErrorCode.OFFER_NEEDS_UPDATE.name])
        assert offer.status == Offer.Status.NEEDS_UPDATE
