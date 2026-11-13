"""Who sees an on-site contact's phone, and when the contact can be changed (hard rule e).

Bookings don't exist yet (BEAV-107 adds them), so these tests use a stand-in
that has the attributes `care_contacts.policy.BookingLike` names.
"""

import datetime as dt
from dataclasses import dataclass

import pytest
from django.contrib.auth.models import AnonymousUser
from django.utils import timezone

from accounts.tests.factories import make_user
from care_contacts.models import CareContact
from care_contacts.policy import (
    assert_contact_deletable,
    assert_contact_editable,
    contact_deletable,
    contact_editable,
    contact_phone_visible_to,
    visible_phone,
)
from care_contacts.tests.factories import make_contact
from core.errors import ApiError, ErrorCode

pytestmark = pytest.mark.django_db

NOW = dt.datetime(2026, 11, 7, 8, 0, tzinfo=dt.UTC)


@dataclass
class FakeBooking:
    care_contact_id: int | None
    poster_id: int
    helper_id: int
    is_active: bool
    slot_start: dt.datetime


def booking_for(contact: CareContact, helper_id: int, **fields: object) -> FakeBooking:
    details: dict[str, object] = {
        "care_contact_id": contact.pk,
        "poster_id": contact.owner_id,
        "helper_id": helper_id,
        "is_active": True,
        "slot_start": NOW + dt.timedelta(days=1),
        **fields,
    }
    return FakeBooking(**details)  # pyright: ignore[reportArgumentType]


@pytest.fixture
def contact() -> CareContact:
    return make_contact(make_user("ana"))


def test_the_owner_always_sees_the_phone(contact: CareContact) -> None:
    assert contact_phone_visible_to(contact.owner, contact, None)
    assert visible_phone(contact.owner, contact, None) == contact.phone_number


def test_the_accepted_helper_sees_it_while_the_booking_is_active(contact: CareContact) -> None:
    marko = make_user("marko")
    booking = booking_for(contact, marko.pk)

    assert contact_phone_visible_to(marko, contact, booking)
    assert visible_phone(marko, contact, booking) == "+385912345678"


def test_the_helper_loses_it_once_the_booking_closes(contact: CareContact) -> None:
    marko = make_user("marko")
    closed = booking_for(contact, marko.pk, is_active=False)

    assert not contact_phone_visible_to(marko, contact, closed)
    assert visible_phone(marko, contact, closed) is None


def test_a_helper_who_only_sent_an_offer_never_sees_it(contact: CareContact) -> None:
    accepted, offering = make_user("marko"), make_user("ivana")
    booking = booking_for(contact, accepted.pk)

    assert visible_phone(offering, contact, booking) is None
    assert visible_phone(offering, contact, None) is None


def test_another_user_never_sees_it(contact: CareContact) -> None:
    stranger = make_user("luka")
    assert visible_phone(stranger, contact, None) is None


def test_signed_out_callers_never_see_it(contact: CareContact) -> None:
    marko = make_user("marko")
    booking = booking_for(contact, marko.pk)

    assert visible_phone(AnonymousUser(), contact, booking) is None
    assert visible_phone(None, contact, booking) is None


def test_a_suspended_helper_doesnt_see_it(contact: CareContact) -> None:
    marko = make_user("marko", is_active=False)
    assert visible_phone(marko, contact, booking_for(contact, marko.pk)) is None


def test_a_booking_for_another_contact_grants_nothing(contact: CareContact) -> None:
    marko = make_user("marko")
    other = make_contact(contact.owner, name="Ujak Ivo", phone_number="+385987654321")
    booking = booking_for(other, marko.pk)

    assert visible_phone(marko, contact, booking) is None
    assert visible_phone(marko, other, booking) == "+385987654321"


def test_the_helper_sees_the_changed_number_at_once(contact: CareContact) -> None:
    marko = make_user("marko")
    booking = booking_for(contact, marko.pk)

    contact.phone_number = "+385981112222"
    contact.save()
    contact.refresh_from_db()

    assert visible_phone(marko, contact, booking) == "+385981112222"


def test_a_contact_is_editable_until_an_active_bookings_slot_starts(
    contact: CareContact,
) -> None:
    booking = booking_for(contact, make_user("marko").pk, slot_start=NOW)

    assert contact_editable(contact, [booking], now=NOW - dt.timedelta(seconds=1))
    assert not contact_editable(contact, [booking], now=NOW)
    assert not contact_editable(contact, [booking], now=NOW + dt.timedelta(hours=3))


def test_it_is_editable_again_once_that_booking_closes(contact: CareContact) -> None:
    closed = booking_for(contact, make_user("marko").pk, slot_start=NOW, is_active=False)
    assert contact_editable(contact, [closed], now=NOW + dt.timedelta(hours=1))


def test_bookings_of_other_contacts_dont_lock_it(contact: CareContact) -> None:
    other = make_contact(contact.owner, name="Ujak Ivo")
    booking = booking_for(other, make_user("marko").pk, slot_start=NOW)

    assert contact_editable(contact, [booking], now=NOW)
    assert contact_editable(contact, [], now=NOW)


def test_one_started_booking_among_several_locks_it(contact: CareContact) -> None:
    marko = make_user("marko")
    later = booking_for(contact, marko.pk, slot_start=NOW + dt.timedelta(days=7))
    started = booking_for(contact, marko.pk, slot_start=NOW - dt.timedelta(minutes=5))

    assert not contact_editable(contact, [later, started], now=NOW)


def test_assert_contact_editable_raises_care_contact_locked(contact: CareContact) -> None:
    booking = booking_for(contact, make_user("marko").pk, slot_start=timezone.now())

    with pytest.raises(ApiError) as raised:
        assert_contact_editable(contact, [booking])
    assert raised.value.code is ErrorCode.CARE_CONTACT_LOCKED
    assert_contact_editable(contact, [])


def test_a_booking_by_another_poster_naming_the_contact_grants_nothing(
    contact: CareContact,
) -> None:
    marko, forger = make_user("marko"), make_user("luka")
    forged = booking_for(contact, marko.pk, poster_id=forger.pk, slot_start=NOW)

    assert visible_phone(marko, contact, forged) is None
    assert contact_editable(contact, [forged], now=NOW)
    assert contact_deletable(contact, [forged])


def test_a_contact_cant_be_deleted_while_an_active_booking_uses_it(contact: CareContact) -> None:
    upcoming = booking_for(contact, make_user("marko").pk, slot_start=NOW + dt.timedelta(days=3))

    assert contact_editable(contact, [upcoming], now=NOW)
    assert not contact_deletable(contact, [upcoming])


def test_it_can_be_deleted_once_its_bookings_close(contact: CareContact) -> None:
    closed = booking_for(contact, make_user("marko").pk, is_active=False)
    other = make_contact(contact.owner, name="Ujak Ivo")
    elsewhere = booking_for(other, make_user("ivana").pk)

    assert contact_deletable(contact, [closed, elsewhere])
    assert contact_deletable(contact, [])


def test_assert_contact_deletable_raises_care_contact_in_use(contact: CareContact) -> None:
    booking = booking_for(contact, make_user("marko").pk)

    with pytest.raises(ApiError) as raised:
        assert_contact_deletable(contact, [booking])
    assert raised.value.code is ErrorCode.CARE_CONTACT_IN_USE
    assert_contact_deletable(contact, [])
