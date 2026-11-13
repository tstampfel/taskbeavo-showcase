"""The poster may change a booked task's access details while the booking is active (user
decision 2026-11-03): audited, announced, and pushed to the accepted helper only, without
the details themselves."""

from collections.abc import Iterator

import pytest
from django.db import transaction
from django.utils import translation

from accounts.models import User
from bookings import state_machine
from bookings.models import Booking, BookingEvent
from bookings.tests.factories import make_booking
from core import outbox
from core.errors import ApiError, ErrorCode
from core.models import Outbox
from notifications.kinds import KINDS
from notifications.models import Channel, Delivery
from notifications.tests.conftest import FakeExpo, register_device
from notifications.tests.conftest import fake_expo as fake_expo
from offers.models import Offer
from offers.tests.factories import make_helper
from tasks import services as tasks
from tasks.models import Task

pytestmark = pytest.mark.django_db

S = Booking.Status
KIND = "booking.access_details_updated"
SECRET = "Gate code 4512, key under the blue pot"


def change(booking: Booking, text: str = SECRET, **more: object) -> None:
    tasks.update_task(
        booking.poster,
        str(booking.task_id),
        tasks.TaskChanges(access_details=text, **more),  # type: ignore[arg-type]
    )


def events(booking: Booking) -> list[BookingEvent]:
    return list(BookingEvent.objects.filter(booking=booking, type="access_details_updated"))


def rows(booking: Booking) -> list[Outbox]:
    return list(
        Outbox.objects.filter(
            topic=state_machine.ACCESS_DETAILS_TOPIC, aggregate_id=str(booking.pk)
        )
    )


# A cancel requested counts only with a pending no-fault request (test_helper_cancel.py).
@pytest.mark.parametrize("status", [s for s in Booking.ACTIVE if s != S.CANCEL_REQUESTED])
def test_the_poster_changes_access_details_while_the_booking_is_active(status: S) -> None:
    booking = make_booking(status)

    change(booking)

    assert Task.objects.get(pk=booking.task_id).access_details == SECRET
    [event] = events(booking)
    assert event.actor_id == booking.poster_id
    assert event.data == {"task_id": str(booking.task_id)}
    [row] = rows(booking)
    assert row.payload == {"booking_id": str(booking.pk), "task_id": str(booking.task_id)}
    assert SECRET not in str(row.payload)
    # The booking itself doesn't move.
    booking.refresh_from_db()
    assert booking.status == status


@pytest.mark.parametrize("status", [User.Status.SUSPENDED, User.Status.DELETION_PENDING])
def test_a_limited_poster_still_changes_the_access_details(status: str) -> None:
    """Finishing an open booking stays possible in limited mode (accounts.states)."""
    booking = make_booking(S.FUNDS_HELD)
    User.objects.filter(pk=booking.poster_id).update(status=status)
    booking = Booking.objects.get(pk=booking.pk)  # with the poster as it is now

    change(booking)

    assert Task.objects.get(pk=booking.task_id).access_details == SECRET
    assert len(events(booking)) == 1


INACTIVE = [
    s for s in S if (s not in Booking.ACTIVE or s == S.CANCEL_REQUESTED) and s != S.PAYMENT_FAILED
]


@pytest.mark.parametrize("status", INACTIVE)
def test_access_details_are_refused_when_the_booking_is_not_active(status: S) -> None:
    booking = make_booking(status)
    before = Task.objects.get(pk=booking.task_id).access_details

    with pytest.raises(ApiError) as exc:
        change(booking)

    assert exc.value.extensions["code"] == ErrorCode.TASK_BOOKED.name
    assert Task.objects.get(pk=booking.task_id).access_details == before
    assert events(booking) == []
    assert rows(booking) == []


def test_other_fields_stay_locked_alongside_access_details() -> None:
    booking = make_booking(S.FUNDS_HELD)

    with pytest.raises(ApiError) as exc:
        change(booking, title="Mow the whole lawn")

    assert exc.value.extensions["code"] == ErrorCode.TASK_BOOKED.name
    assert events(booking) == []


def test_the_same_details_again_write_nothing() -> None:
    booking = make_booking(S.FUNDS_HELD)
    change(booking)

    change(booking)

    assert len(events(booking)) == 1
    assert len(rows(booking)) == 1


def test_the_offers_stay_as_they_are() -> None:
    booking = make_booking(S.FUNDS_HELD)

    change(booking)

    offer = Offer.objects.get(pk=booking.offer_id)
    assert (offer.status, offer.task_changed) == (Offer.Status.ACCEPTED, False)


def test_the_handler_says_nothing_once_the_booking_closed() -> None:
    booking = make_booking(S.FUNDS_HELD)
    change(booking)
    Booking.objects.filter(pk=booking.pk).update(status=S.PAID_OUT)

    outbox.publish_row(rows(booking)[0].pk)

    assert not Delivery.objects.exists()


def test_recording_outside_the_lock_is_refused() -> None:
    booking = make_booking(S.AWAITING_PAYMENT)
    with transaction.atomic(), pytest.raises(RuntimeError):
        state_machine.record_access_details_change(booking, booking.poster)


@pytest.mark.parametrize("locale", ["hr", "en", "de", "fr"])
def test_the_push_text_never_holds_the_details(locale: str) -> None:
    kind = KINDS[KIND]
    with translation.override(locale):
        text = f"{kind.title} {kind.body}"
    assert "{" not in text
    assert SECRET not in text
    assert kind.route == "/bookings/{id}"


@pytest.fixture
def other_helper() -> Iterator[User]:
    yield make_helper("ivan")


@pytest.mark.django_db(transaction=True)
def test_only_the_accepted_helper_gets_the_push(
    fake_expo: FakeExpo,
    other_helper: User,
) -> None:
    booking = make_booking(S.FUNDS_HELD)
    helper = User.objects.get(pk=booking.helper_id)
    poster = User.objects.get(pk=booking.poster_id)
    register_device(helper, "ExponentPushToken[marko-phone-0001]")
    register_device(poster, "ExponentPushToken[ana-phone-0001]")
    register_device(other_helper, "ExponentPushToken[ivan-phone-0001]")

    change(booking)
    outbox.publish_row(rows(booking)[0].pk)

    assert {d.user_id for d in Delivery.objects.filter(kind=KIND)} == {helper.pk}
    assert set(Delivery.objects.filter(kind=KIND).values_list("channel", flat=True)) == {
        Channel.PUSH
    }
    [message] = fake_expo.sent
    assert message.to == "ExponentPushToken[marko-phone-0001]"
    assert SECRET not in f"{message.title} {message.body} {message.data}"
    assert message.data["route"] == f"/bookings/{booking.pk}"
