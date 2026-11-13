"""Hard rule e through the real models: who sees a booked task's exact place, access
details and on-site contact (with its phone), and when the contact may change."""

import datetime as dt
from typing import Any

import pytest
from django.test import Client
from django.utils import timezone

from accounts.models import User
from accounts.tests.factories import access_token, make_user
from accounts.tests.test_schema import error_codes, gql
from bookings import state_machine
from bookings.models import Booking
from bookings.tests.factories import book, make_booking
from care_contacts import services as contacts
from care_contacts.models import CareContact
from care_contacts.tests.factories import make_contact
from core.errors import ApiError, ErrorCode
from core.models import ActorKind
from offers import services as offers
from offers.tests.factories import make_helper, open_task
from tasks.tests.factories import make_poster

pytestmark = pytest.mark.django_db

S = Booking.Status
EXACT = "address lat lng accessDetails onSiteContact { id name area language phone }"
TASK = f"query($id: ID!) {{ task(id: $id) {{ id {EXACT} }} }}"


def booked(status: S = S.FUNDS_HELD, **fields: Any) -> tuple[Booking, CareContact]:
    poster = make_poster("ana")
    contact = make_contact(poster)
    booking = make_booking(status, poster=poster, care_contact_id=str(contact.pk), **fields)
    return booking, contact


def seen_by(client: Client, booking: Booking, viewer: User | None) -> dict[str, Any]:
    token = access_token(viewer) if viewer else None
    body = gql(client, TASK, {"id": str(booking.task_id)}, token)
    assert "errors" not in body, error_codes(body)
    return body["data"]["task"]


def hidden(task: dict[str, Any]) -> bool:
    return all(task[f] is None for f in ("address", "lat", "lng", "accessDetails", "onSiteContact"))


def test_the_poster_sees_everything(client: Client) -> None:
    booking, contact = booked()

    task = seen_by(client, booking, booking.poster)

    assert task["address"] and task["accessDetails"]
    assert task["onSiteContact"] == {
        "id": str(contact.pk),
        "name": "Mama Kata",
        "area": "Gruž, Dubrovnik",
        "language": "HR",
        "phone": "+385912345678",
    }


# CANCEL_REQUESTED is active only with a pending no-fault request: test_helper_cancel.py.
@pytest.mark.parametrize("status", [s for s in Booking.ACTIVE if s != S.CANCEL_REQUESTED])
def test_the_helper_of_an_active_booking_sees_the_place_and_phone(
    client: Client, status: S
) -> None:
    booking, _ = booked(status)

    task = seen_by(client, booking, booking.helper)

    assert task["address"] == "Ulica Ante Starčevića 1, Dubrovnik"
    assert task["accessDetails"] == "Gate code 1234, key under the mat"
    assert task["lat"] is not None
    assert task["onSiteContact"]["phone"] == "+385912345678"


@pytest.mark.parametrize(
    "status", [s for s in S if s not in Booking.ACTIVE or s == S.CANCEL_REQUESTED]
)
def test_the_helper_sees_nothing_exact_outside_an_active_booking(client: Client, status: S) -> None:
    """Before the payment is confirmed, and once the booking closes."""
    booking, _ = booked(status)

    assert hidden(seen_by(client, booking, booking.helper))


def test_the_helper_loses_the_place_when_the_booking_settles(client: Client) -> None:
    booking, _ = booked(S.PAYMENT_REQUESTED)
    assert not hidden(seen_by(client, booking, booking.helper))

    state_machine.transition(
        booking.pk, S.RELEASED, actor_kind=ActorKind.POSTER, actor=booking.poster
    )

    assert hidden(seen_by(client, booking, booking.helper))


def test_others_see_nothing_exact(client: Client) -> None:
    poster = make_poster("ana")
    contact = make_contact(poster)
    task = open_task(poster, care_contact_id=str(contact.pk))
    offering = make_helper("ivan")
    offers.send_offer(offering, str(task.pk), 2_000)
    accepted = offers.send_offer(make_helper(), str(task.pk), 3_000)
    booking = book(poster, accepted)
    Booking.objects.filter(pk=booking.pk).update(status=S.FUNDS_HELD)

    for viewer in (offering, make_user("stranger"), None):
        assert hidden(seen_by(client, booking, viewer))


def test_a_contact_another_poster_owns_shows_nothing(client: Client) -> None:
    booking, _ = booked()
    foreign = make_contact(make_poster("eve"), name="Not yours")
    Booking.objects.filter(pk=booking.pk).update(care_contact=foreign)

    task = seen_by(client, booking, booking.helper)

    assert task["address"] is not None
    assert task["onSiteContact"] is None


def test_the_helper_keeps_the_contact_of_the_booking() -> None:
    """The booking's contact is fixed when the offer is accepted."""
    booking, contact = booked()

    assert booking.care_contact_id == contact.pk


# The contact's edit lock and delete refusal.


def started(booking: Booking) -> None:
    Booking.objects.filter(pk=booking.pk).update(
        slot_start=timezone.now() - dt.timedelta(minutes=5)
    )


def test_the_contact_is_editable_before_the_slot_starts() -> None:
    booking, contact = booked()

    changed = contacts.update_contact(
        booking.poster, str(contact.pk), contacts.ContactChanges(area="Lapad")
    )

    assert changed.area == "Lapad"


def test_the_contact_is_locked_from_the_slot_start_until_the_booking_closes() -> None:
    booking, contact = booked()
    started(booking)

    with pytest.raises(ApiError) as exc:
        contacts.update_contact(
            booking.poster, str(contact.pk), contacts.ContactChanges(area="Lapad")
        )
    assert exc.value.extensions["code"] == ErrorCode.CARE_CONTACT_LOCKED.name

    Booking.objects.filter(pk=booking.pk).update(status=S.CLOSED)
    contacts.update_contact(booking.poster, str(contact.pk), contacts.ContactChanges(area="Lapad"))


def test_an_active_booking_keeps_its_contact() -> None:
    booking, contact = booked()

    with pytest.raises(ApiError) as exc:
        contacts.delete_contact(booking.poster, str(contact.pk))

    assert exc.value.extensions["code"] == ErrorCode.CARE_CONTACT_IN_USE.name
    assert CareContact.objects.filter(pk=contact.pk).exists()


def test_a_contact_is_deleted_once_its_booking_and_task_closed() -> None:
    booking, contact = booked()
    Booking.objects.filter(pk=booking.pk).update(status=S.CLOSED)
    booking.task.status = "CLOSED"
    booking.task.save(update_fields=["status"])

    contacts.delete_contact(booking.poster, str(contact.pk))

    booking.refresh_from_db()
    assert booking.care_contact_id is None
    assert not CareContact.objects.filter(pk=contact.pk).exists()


@pytest.mark.parametrize(
    "status", [S.AWAITING_PAYMENT, S.DISPUTED, S.INCIDENT_PAUSED, S.RELEASED, S.REFUNDED]
)
def test_a_booking_not_yet_closed_keeps_its_contact(status: S) -> None:
    """Also when the task closed: the contact may be dispute evidence."""
    booking, contact = booked(status)
    booking.task.status = "CLOSED"
    booking.task.save(update_fields=["status"])

    with pytest.raises(ApiError) as exc:
        contacts.delete_contact(booking.poster, str(contact.pk))

    assert exc.value.extensions["code"] == ErrorCode.CARE_CONTACT_IN_USE.name


def test_the_booked_helper_keeps_the_task_once_it_closes(client: Client) -> None:
    """A later ticket may close the task once it is paid; the active helper keeps it."""
    booking, _ = booked(S.FUNDS_HELD)
    booking.task.status = "CLOSED"
    booking.task.save(update_fields=["status"])

    assert not hidden(seen_by(client, booking, booking.helper))
    for viewer in (make_helper("ivan"), None):
        token = access_token(viewer) if viewer else None
        body = gql(client, TASK, {"id": str(booking.task_id)}, token)
        assert error_codes(body) == [ErrorCode.TASK_NOT_FOUND.name]

    Booking.objects.filter(pk=booking.pk).update(status=S.CLOSED)
    body = gql(client, TASK, {"id": str(booking.task_id)}, access_token(booking.helper))
    assert error_codes(body) == [ErrorCode.TASK_NOT_FOUND.name]


def test_the_helper_keeps_the_place_during_a_safety_incident(client: Client) -> None:
    """User decision 2026-11-03: the helper may be on site calling 112."""
    booking, _ = booked(S.FUNDS_HELD)
    state_machine.transition(
        booking.pk, S.INCIDENT_PAUSED, actor_kind=ActorKind.HELPER, actor=booking.helper
    )

    task = seen_by(client, booking, booking.helper)
    assert task["address"] == "Ulica Ante Starčevića 1, Dubrovnik"
    assert task["onSiteContact"]["phone"] == "+385912345678"

    # Support settles it: a dispute, then (BEAV-126) a decision.
    staff = make_user("staff", is_staff=True)
    state_machine.transition(booking.pk, S.DISPUTED, actor_kind=ActorKind.ADMIN, actor=staff)
    assert hidden(seen_by(client, booking, booking.helper))
