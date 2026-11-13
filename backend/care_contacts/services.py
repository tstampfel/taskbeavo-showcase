"""Create, change and delete a poster's on-site contacts. Only the owner can do any of it.

The phone may be any valid number, mobile or landline (care_contacts/phone.py). Never log
the name or the phone.

A contact named by a draft, held or open task can't be deleted (CARE_CONTACT_IN_USE);
the owner's closed tasks let go of it (`Task.care_contact` set to NULL) and it is deleted.
`Task.care_contact` is `on_delete=RESTRICT`, so nothing else removes a contact under a
task, while deleting the owner's account removes tasks and contacts in one cascade.

Bookings (`care_contacts.policy`): both changes lock the contact, then its active
bookings (`select_for_update`), in that order. `update_contact` refuses from the slot
start of an active booking until it closes (CARE_CONTACT_LOCKED); `delete_contact`
refuses while any booking using the contact isn't closed yet (CARE_CONTACT_IN_USE: active,
in checkout, disputed or paused) and otherwise clears it on the closed ones, which are
`on_delete=RESTRICT` too.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone

from accounts.models import User
from care_contacts import policy
from care_contacts.models import CareContact
from care_contacts.phone import normalize_contact_phone
from core.errors import ApiError, ErrorCode

if TYPE_CHECKING:
    from bookings.models import Booking

_MAX_TEXT = 100


@dataclass(frozen=True)
class ContactChanges:
    """Fields to set; None leaves a field as it is."""

    name: str | None = None
    phone: str | None = None
    area: str | None = None
    language: str | None = None
    no_smartphone: bool | None = None


def create_contact(
    owner: User, details: ContactChanges, *, has_their_agreement: bool
) -> CareContact:
    if not has_their_agreement:
        raise ApiError(
            ErrorCode.CARE_CONTACT_AGREEMENT_REQUIRED,
            "Confirm you have the person's agreement to save their details.",
        )
    if details.name is None or details.phone is None or details.area is None:
        raise ApiError(ErrorCode.INVALID_CARE_CONTACT, "Name, phone and area are required.")
    contact = CareContact(owner=owner, agreement_confirmed_at=timezone.now())
    _apply(contact, details)
    contact.save()
    return contact


def update_contact(owner: User, contact_id: str, changes: ContactChanges) -> CareContact:
    with transaction.atomic():
        contact = _owned(owner, contact_id, lock=True)
        policy.assert_contact_editable(contact, _active_bookings(contact))
        _apply(contact, changes)
        contact.save()
    return contact


def delete_contact(owner: User, contact_id: str) -> CareContact:
    """Delete the contact; returns it as it was, with its ID."""
    # Imported here: tasks.models and bookings.models import this app's models.
    from bookings.models import Booking
    from tasks.models import Task

    with transaction.atomic():
        contact = _owned(owner, contact_id, lock=True)
        policy.assert_contact_deletable(contact, _active_bookings(contact))
        # A booking not yet closed (in checkout, disputed, paused) keeps it as evidence.
        if (
            Booking.objects.filter(care_contact=contact)
            .exclude(status__in=Booking.TERMINAL)
            .exists()
        ):
            raise ApiError(
                ErrorCode.CARE_CONTACT_IN_USE,
                "A booking with this contact is still open; delete it after the booking closes.",
            )
        tasks = Task.objects.select_for_update().filter(care_contact=contact)
        if tasks.exclude(status=Task.Status.CLOSED).exists():
            raise ApiError(
                ErrorCode.CARE_CONTACT_IN_USE,
                "An open task uses this contact; delete it after the task closes.",
            )
        tasks.update(care_contact=None)
        Booking.objects.filter(care_contact=contact).update(care_contact=None)
        contact_pk = contact.pk
        contact.delete()
    contact.pk = contact_pk
    return contact


def _active_bookings(contact: CareContact) -> list["Booking"]:
    """The contact's active bookings, locked until the transaction ends."""
    from bookings.queries import active_bookings_for_contact

    return active_bookings_for_contact(contact, lock=True)


def _owned(owner: User, contact_id: str, *, lock: bool = False) -> CareContact:
    """The owner's contact; CARE_CONTACT_NOT_FOUND for anyone else's, so IDs reveal nothing."""
    queryset = CareContact.objects.filter(owner=owner)
    if lock:
        queryset = queryset.select_for_update()
    try:
        return queryset.get(pk=int(contact_id))
    except (ValueError, CareContact.DoesNotExist):
        raise ApiError(ErrorCode.CARE_CONTACT_NOT_FOUND, "No such contact.") from None


def _apply(contact: CareContact, changes: ContactChanges) -> None:
    if changes.name is not None:
        contact.name = _text(changes.name)
    if changes.area is not None:
        contact.area = _text(changes.area)
    if changes.phone is not None:
        phone = normalize_contact_phone(changes.phone)
        contact.phone_number = phone.e164
        contact.sms_capable = phone.sms_capable
    if changes.language is not None:
        contact.language = changes.language
    if changes.no_smartphone is not None:
        contact.no_smartphone = changes.no_smartphone


def _text(value: str) -> str:
    text = value.strip()
    if not text or len(text) > _MAX_TEXT:
        raise ApiError(
            ErrorCode.INVALID_CARE_CONTACT, f"Name and area take 1 to {_MAX_TEXT} characters."
        )
    return text
