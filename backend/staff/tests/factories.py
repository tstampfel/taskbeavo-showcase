"""Test staff members, and a booking with everything the evidence view shows."""

from itertools import count
from typing import Any

from django.contrib.auth.models import Group

from accounts.models import User
from accounts.tests.factories import make_user
from bookings.models import Booking
from bookings.tests.factories import make_booking
from care_contacts.tests.factories import make_contact
from staff.roles import Role
from tasks.models import TaskPhoto
from tasks.tests.factories import ready_photo

_numbers = count(1)

ADDRESS = "Ulica Ante Starčevića 1, Dubrovnik"
ACCESS = "Gate code 1234, key under the mat"
CONTACT_PHONE = "+385912345678"


def make_staff(*roles: Role, **fields: Any) -> User:
    fields.setdefault("is_staff", True)
    user = make_user(f"staff{next(_numbers)}", **fields)
    for role in roles:
        user.groups.add(Group.objects.get(name=role))
    return user


def evidence_booking(status: Booking.Status = Booking.Status.FUNDS_HELD) -> Booking:
    """An active booking with an on-site contact and a task photo."""
    booking = make_booking(status)
    contact = make_contact(booking.poster, phone_number=CONTACT_PHONE)
    Booking.objects.filter(pk=booking.pk).update(
        care_contact=contact, payment_intent_id="pi_test_evidence"
    )
    photo = ready_photo(booking.poster)
    TaskPhoto.objects.create(task=booking.task, upload=photo, position=0)
    booking.refresh_from_db()
    return booking
