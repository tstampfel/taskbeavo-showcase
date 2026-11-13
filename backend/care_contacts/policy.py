"""Who sees an on-site contact's phone, and when the contact may be changed.

Hard rule e: the phone is visible to the poster who owns the contact and to the helper of
an accepted booking for that contact while the booking is active. Nobody else sees it:
not helpers who only sent an offer, not other users, not signed-out callers, and not the
helper once the booking closes.

Edits are live: there is one contact record, so the accepted helper always reads the
current phone. A contact is locked from the slot start of any of its active bookings
until that booking closes, so the person the helper is about to meet can't change under
them mid-visit. It can't be deleted at all while an active booking uses it.

A booking counts for a contact only when it names the contact and its poster owns the
contact. A booking that names someone else's contact (a bug, or a forged ID) grants no
phone and locks nothing.

Bookings arrive with BEAV-107 and the booking tickets. These functions take any object
with the attributes of `BookingLike`, so the booking model only has to provide them (as
fields or properties) and pass its bookings in.
"""

import datetime as dt
from collections.abc import Iterable
from typing import Protocol

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.utils import timezone

from accounts.models import User
from care_contacts.models import CareContact
from core.errors import ApiError, ErrorCode


class BookingLike(Protocol):
    """What the policy needs from a booking."""

    @property
    def care_contact_id(self) -> int | None: ...

    @property
    def poster_id(self) -> int: ...

    @property
    def helper_id(self) -> int: ...

    @property
    def is_active(self) -> bool:
        """Accepted and not yet closed (completed, cancelled, refunded)."""
        ...

    @property
    def slot_start(self) -> dt.datetime: ...


def contact_phone_visible_to(
    viewer: AbstractBaseUser | AnonymousUser | None,
    contact: CareContact,
    booking: BookingLike | None,
) -> bool:
    """Whether `viewer` may see `contact`'s phone, in the context of `booking` if any."""
    if not isinstance(viewer, User) or not viewer.is_active:
        return False
    if viewer.pk == contact.owner_id:
        return True
    if booking is None or not _is_for(booking, contact):
        return False
    return booking.is_active and booking.helper_id == viewer.pk


def visible_phone(
    viewer: AbstractBaseUser | AnonymousUser | None,
    contact: CareContact,
    booking: BookingLike | None,
) -> str | None:
    """The contact's current phone for `viewer`, or None when they may not see it."""
    return contact.phone_number if contact_phone_visible_to(viewer, contact, booking) else None


def contact_editable(
    contact: CareContact, bookings: Iterable[BookingLike], *, now: dt.datetime | None = None
) -> bool:
    """False while any active booking for `contact` has reached its slot start."""
    now = now or timezone.now()
    return not any(
        _is_for(booking, contact) and booking.is_active and booking.slot_start <= now
        for booking in bookings
    )


def assert_contact_editable(
    contact: CareContact, bookings: Iterable[BookingLike], *, now: dt.datetime | None = None
) -> None:
    """Raise CARE_CONTACT_LOCKED unless `contact_editable`."""
    if not contact_editable(contact, bookings, now=now):
        raise ApiError(
            ErrorCode.CARE_CONTACT_LOCKED,
            "A visit with this contact has started; change it after the booking closes.",
        )


def contact_deletable(contact: CareContact, bookings: Iterable[BookingLike]) -> bool:
    """False while any active booking uses `contact`, before or after its slot start."""
    return not any(_is_for(booking, contact) and booking.is_active for booking in bookings)


def assert_contact_deletable(contact: CareContact, bookings: Iterable[BookingLike]) -> None:
    """Raise CARE_CONTACT_IN_USE unless `contact_deletable`."""
    if not contact_deletable(contact, bookings):
        raise ApiError(
            ErrorCode.CARE_CONTACT_IN_USE,
            "An active booking uses this contact; delete it after the booking closes.",
        )


def _is_for(booking: BookingLike, contact: CareContact) -> bool:
    """The booking names `contact` and was posted by the contact's owner."""
    return booking.care_contact_id == contact.pk and booking.poster_id == contact.owner_id
