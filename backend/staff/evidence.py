"""What a staff member sees of a booking: the dispute evidence (plan: "Disputes": the chat,
the photos and the log of payment requests, reminders and cancellations), cut down to
their role (`staff.roles`), and the audit row that says they looked.

Read-only. Building the view never changes the booking, its payment or the retention or
legal hold of its photos (BEAV-63): disputes and holds are BEAV-126, money actions
BEAV-157. Later tickets add their evidence here (chat, no-access photos, incidents).
"""

import copy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction

from bookings.cancellation import Case, NoFaultReason, no_fault_label
from bookings.helper_cancel import HelperCancelReason
from bookings.models import Booking, BookingEvent, ConsentRecord
from staff import audit
from staff.masking import mask
from staff.models import StaffAction, StaffNote
from staff.roles import Capability, can, is_staff
from tasks.models import TaskPhoto
from uploads import storage
from uploads.models import Status as UploadStatus
from uploads.models import Upload

NOTE_MAX_LENGTH = 5_000
_BOOKING = Booking._meta.label_lower
# Cancel reasons that can reveal health data (GDPR Art. 9): only staff granted
# `Capability.NO_FAULT_DETAIL` see them (BEAV-123). Everyone else sees a label that also
# covers a reason that isn't health, so it can't be worked back: a no-fault reason shows as
# the other side sees it (`no_fault_label`: hospitalisation, bereavement and emergency are
# all PERSONAL_EMERGENCY), and a helper's ILL or OTHER both as PERSONAL_REASON.
_HELPER_PERSONAL = frozenset({HelperCancelReason.ILL.value, HelperCancelReason.OTHER.value})
HELPER_PERSONAL_LABEL = "PERSONAL_REASON"


@dataclass
class Place:
    exact: bool
    area: str
    address: str = ""
    access_details: str = ""


@dataclass
class Contact:
    # Empty unless the role sees the exact place.
    name: str
    phone: str
    phone_masked: bool


@dataclass
class Payments:
    amount_cents: int
    fee_cents: int
    tip_cents: int
    refund_cents: int | None
    helper_compensation_cents: int | None
    # None: the role doesn't see Stripe IDs.
    payment_intent_id: str | None = None
    late_payment_refund_id: str | None = None
    stripe_account_id: str | None = None
    account_status: str | None = None


@dataclass
class Photo:
    id: str
    purpose: str
    url: str | None
    received_at: datetime | None


@dataclass
class BookingEvidence:
    booking: Booking
    helper_is_trader: bool
    place: Place
    contact: Contact | None
    events: list[BookingEvent]
    consent: ConsentRecord | None
    payments: Payments
    # None: the role doesn't see them.
    photos: list[Photo] | None
    chat: list[Any] | None
    notes: list[StaffNote]
    can_write_restricted: bool
    sections: list[str] = field(default_factory=list)


def booking_evidence(user: Any, booking: Booking) -> BookingEvidence:
    """The evidence `user` may see, and a VIEW_BOOKING_EVIDENCE audit row naming which
    sections were shown."""
    if not is_staff(user):
        raise PermissionDenied
    sees = {capability for capability in Capability if can(user, capability)}
    task = booking.task
    profile = getattr(booking.helper, "helper_profile", None)

    area = task.service_area.name if task.service_area else ""
    place = Place(exact=False, area=area)
    if Capability.EXACT_PLACE in sees:
        place = Place(
            exact=True, area=area, address=task.address, access_details=task.access_details
        )

    contact = None
    if booking.care_contact is not None:
        full_phone = Capability.CONTACT_PHONE in sees
        contact = Contact(
            name=booking.care_contact.name if Capability.EXACT_PLACE in sees else "",
            phone=(
                booking.care_contact.phone_number
                if full_phone
                else mask(booking.care_contact.phone_number, 3)
            ),
            phone_masked=not full_phone,
        )

    payments = Payments(
        amount_cents=booking.amount_cents,
        fee_cents=booking.fee_cents,
        tip_cents=booking.tip_cents,
        refund_cents=booking.refund_cents,
        helper_compensation_cents=booking.helper_compensation_cents,
    )
    if Capability.STRIPE_IDS in sees:
        payments.payment_intent_id = booking.payment_intent_id
        payments.late_payment_refund_id = booking.late_payment_refund_id
        account = getattr(profile, "connected_account", None) if profile else None
        if account is not None:
            payments.stripe_account_id = account.stripe_account_id
            payments.account_status = account.status

    photos = None
    if Capability.PHOTOS in sees:
        # TODO(BEAV-116, BEAV-125, BEAV-126): add the booking's evidence photos once
        # bookings link them.
        task_photos = TaskPhoto.objects.filter(task=task).select_related("upload")
        uploads = [task_photo.upload for task_photo in task_photos]
        photos = [_photo(upload) for upload in uploads]

    # TODO(chat ticket): no chat model yet; the chat's messages go here.
    chat: list[Any] | None = [] if Capability.CHAT in sees else None

    notes = StaffNote.objects.filter(object_type=_BOOKING, object_id=str(booking.pk))
    if Capability.RESTRICTED_NOTES not in sees:
        notes = notes.filter(restricted=False)

    evidence = BookingEvidence(
        booking=booking,
        helper_is_trader=bool(profile and profile.is_trader),
        place=place,
        contact=contact,
        events=_events(
            BookingEvent.objects.filter(booking=booking), Capability.NO_FAULT_DETAIL in sees
        ),
        consent=ConsentRecord.objects.filter(booking=booking).first(),
        payments=payments,
        photos=photos,
        chat=chat,
        notes=list(notes),
        can_write_restricted=Capability.RESTRICTED_NOTES in sees,
        sections=sorted(capability.value for capability in sees),
    )
    audit.record(user, StaffAction.VIEW_BOOKING_EVIDENCE, booking, sections=evidence.sections)
    return evidence


def _photo(upload: Upload) -> Photo:
    url = None
    if upload.status == UploadStatus.READY and upload.display_key:
        # The private clean copy: short-lived, never the EXIF-bearing original.
        url = storage.presigned_get(settings.R2_PRIVATE_BUCKET, upload.display_key)
    return Photo(str(upload.pk), upload.purpose, url, upload.received_at)


def add_note(user: Any, booking: Booking, body: str, *, restricted: bool = False) -> StaffNote:
    """A staff note on `booking`. Only the trust role may write a restricted one."""
    if not is_staff(user):
        raise PermissionDenied
    if restricted and not can(user, Capability.RESTRICTED_NOTES):
        raise PermissionDenied
    body = body.strip()
    if not body or len(body) > NOTE_MAX_LENGTH:
        raise ValueError("A staff note needs 1 to 5000 characters.")
    siblings = StaffNote.objects.filter(object_type=_BOOKING, object_id=str(booking.pk))
    with transaction.atomic():
        # Join the booking's retention (staff.retention): held during a dispute, and
        # kept as long as its other notes after a hold was lifted.
        dates = [d for d in siblings.values_list("delete_after", flat=True) if d is not None]
        note = StaffNote.objects.create(
            object_type=_BOOKING,
            object_id=str(booking.pk),
            author=user,
            body=body,
            restricted=restricted,
            legal_hold=(
                booking.status == Booking.Status.DISPUTED
                or siblings.filter(legal_hold=True).exists()
            ),
            delete_after=max(dates, default=None),
        )
        # Named by the note, without `restricted`: the log never hints that a restricted
        # note exists on a booking (review BEAV-156 R1-2).
        audit.record(user, StaffAction.ADD_NOTE, note)
    return note


def no_fault_reason_for(user: Any, reason: str) -> str:
    """A no-fault request's `reason` as `user` may see it."""
    if can(user, Capability.NO_FAULT_DETAIL):
        return reason
    return no_fault_label(NoFaultReason(reason)).value


def _events(events: Any, specific: bool) -> list[BookingEvent]:
    if specific:
        return list(events)
    shown = []
    for event in events:
        data = event.data if isinstance(event.data, dict) else {}
        # A helper cancel's transition (`helper_cancel.cancel_as_helper`).
        if data.get("case") == Case.HELPER_CANCEL and data.get("reason") in _HELPER_PERSONAL:
            # A copy for display only: the stored row keeps what happened.
            event = copy.copy(event)
            event.data = {**data, "reason": HELPER_PERSONAL_LABEL}
        shown.append(event)
    return shown
