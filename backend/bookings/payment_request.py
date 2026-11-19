"""The helper's "Request payment" (PLAN.md, "The adapted flow keeps Airtasker's release
model"; BEAV-116).

`request_payment` (the requestPayment mutation): the helper of a booking whose money is
held (FUNDS_HELD) says the job is done, optionally with up to `MAX_PHOTOS` photos of it
(their own EVIDENCE_PHOTO uploads, re-encoded without metadata) and a note of up to
`NOTE_MAX_LENGTH` characters. The booking moves to PAYMENT_REQUESTED through the state
machine (hard rule c): one transaction, the booking locked, a BookingEvent and outbox rows.
It changes state only: no Stripe call (hard rule a). Releasing is the poster's next tap
(BEAV-117), which the state machine allows only from PAYMENT_REQUESTED.

When: once the job has started, at or after `Booking.slot_start` (a confirmed early start
moves it, bookings/arrival.py). Earlier it is refused with PAYMENT_REQUEST_TOO_EARLY: a
payment request ends the poster's right to cancel (cancellation policy, "Helper has
already requested payment"), so it must not be possible before the job.

A registered business (the helper type the poster contracted on at checkout,
`ConsentRecord.helper_is_trader`) owes the poster its own fiscalized invoice for the task
(PLAN.md, "Obrt helper finishes a job"). It adds the invoice first, as a photo or scan
(an INVOICE upload) or by its JIR. Only the JIR's format is checked: TaskBeavo never calls
CIS or any government service (owner decision 2026-11-14). A private helper skips this
step and may not add an invoice.

`REQUESTED_TOPIC` tells the poster: a push and an email (`booking.payment_requested`,
always emailed, in their language). The live event to both sides is the state machine's
`booking.updated`.

The note is the helper's free text: it is stored and shown to the booking's two sides
only, never put in BookingEvent data, logs or telemetry. Booked sides aren't filtered for
contact details (moderation/contact_filter.py).
"""

import datetime as dt
import re
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from accounts.models import User
from bookings import state_machine
from bookings.cancel import _pk, helper_is_trader
from bookings.models import Booking, PaymentRequest, PaymentRequestPhoto
from core.errors import ApiError, ErrorCode
from core.models import ActorKind
from core.outbox import enqueue
from uploads import services as uploads
from uploads.models import Purpose

S = Booking.Status

REQUESTED_TOPIC = "booking.payment_requested"
NOTE_MAX_LENGTH = 1000
MAX_PHOTOS = 6
# A JIR is a UUID in its 8-4-4-4-12 form (Porezna uprava, fiscalization technical spec).
_JIR = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def request_payment(
    helper: User,
    booking_id: Any,
    *,
    note: str = "",
    photo_ids: Sequence[UUID] = (),
    invoice_upload_id: UUID | None = None,
    invoice_jir: str | None = None,
    now: dt.datetime | None = None,
) -> Booking:
    """The helper requests payment (module docstring). Returns the booking,
    PAYMENT_REQUESTED."""
    now = now or timezone.now()
    note = note.strip()
    photos = list(dict.fromkeys(photo_ids))
    jir = _jir(invoice_jir)
    if len(note) > NOTE_MAX_LENGTH:
        raise _invalid(f"The note is longer than {NOTE_MAX_LENGTH} characters.")
    if len(photos) > MAX_PHOTOS:
        raise _invalid(f"At most {MAX_PHOTOS} photos.")
    with transaction.atomic():
        try:
            booking = Booking.objects.select_for_update().get(pk=_pk(booking_id))
        except Booking.DoesNotExist:
            raise ApiError(ErrorCode.BOOKING_NOT_FOUND, "No such booking.") from None
        if booking.helper_id != helper.pk:
            raise ApiError(ErrorCode.BOOKING_NOT_FOUND, "No such booking.")
        if not _held(booking):
            raise ApiError(
                ErrorCode.BOOKING_ACTION_NOT_ALLOWED, "This booking isn't waiting for its job."
            )
        if now < booking.slot_start:
            raise ApiError(ErrorCode.PAYMENT_REQUEST_TOO_EARLY, "The job hasn't started yet.")
        trader = helper_is_trader(booking)
        has_invoice = invoice_upload_id is not None or bool(jir)
        if trader and not has_invoice:
            raise ApiError(ErrorCode.INVOICE_REQUIRED, "Add your fiscalized invoice first.")
        if not trader and has_invoice:
            raise _invalid("A private helper adds no invoice.")
        booking = state_machine.transition(
            booking.pk,
            S.PAYMENT_REQUESTED,
            actor_kind=ActorKind.HELPER,
            actor=helper,
            # Counts and enum keys only: never the note or the JIR.
            data={
                "photos": len(photos),
                "commented": bool(note),
                "invoice": "UPLOAD" if invoice_upload_id else "JIR" if jir else None,
            },
            now=now,
        )
        request = PaymentRequest.objects.create(
            booking=booking,
            note=note,
            invoice_jir=jir,
            invoice_upload_id=_claim_invoice(helper, invoice_upload_id),
            created_at=now,
        )
        _claim_photos(helper, request, photos)
        enqueue(
            REQUESTED_TOPIC,
            aggregate=booking,
            payload={"booking_id": str(booking.pk), "task_id": str(booking.task_id)},
        )
    return booking


def can_request(booking: Booking, viewer: User, now: dt.datetime | None = None) -> bool:
    """Whether `viewer` gets the "Request payment" button now: the booking's helper, while
    the money is held and once the job has started."""
    return (
        booking.helper_id == viewer.pk
        and _held(booking)
        and (now or timezone.now()) >= booking.slot_start
    )


def _held(booking: Booking) -> bool:
    return booking.status == S.FUNDS_HELD and booking.task_reopened_at is None


def _jir(value: str | None) -> str:
    if value is None:
        return ""
    jir = value.strip().lower()
    if not _JIR.fullmatch(jir):
        raise _invalid("That isn't a JIR.")
    return jir


def _claim_invoice(helper: User, upload_id: UUID | None) -> UUID | None:
    if upload_id is None:
        return None
    try:
        uploads.claim_uploads(helper, [upload_id], Purpose.INVOICE)
    except uploads.UploadError as error:
        raise ApiError(error.code, "Invoice not available.") from None
    return upload_id


def _claim_photos(helper: User, request: PaymentRequest, photo_ids: list[UUID]) -> None:
    try:
        uploads.claim_uploads(helper, photo_ids, Purpose.EVIDENCE_PHOTO)
    except uploads.UploadError as error:
        raise ApiError(error.code, "Photo not available.") from None
    PaymentRequestPhoto.objects.bulk_create(
        PaymentRequestPhoto(payment_request=request, upload_id=pk, position=position)
        for position, pk in enumerate(photo_ids)
    )


def _invalid(message: str) -> ApiError:
    return ApiError(ErrorCode.INVALID_PAYMENT_REQUEST, message)
