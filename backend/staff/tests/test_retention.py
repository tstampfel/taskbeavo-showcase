import datetime as dt

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from bookings import state_machine
from bookings.models import Booking
from core.models import ActorKind
from staff import evidence, retention
from staff.models import StaffAction, StaffAuditEvent, StaffNote
from staff.roles import Role
from staff.tests.factories import evidence_booking, make_staff

pytestmark = pytest.mark.django_db

BODY = "Mama seemed unwell when the helper arrived."
EVIDENCE_DAYS = dt.timedelta(days=settings.UPLOAD_EVIDENCE_RETENTION_DAYS)


def note_on(booking: Booking, *, restricted: bool = True) -> StaffNote:
    return evidence.add_note(make_staff(Role.TRUST), booking, BODY, restricted=restricted)


def close(booking: Booking, at: dt.datetime) -> None:
    Booking.objects.filter(pk=booking.pk).update(status=Booking.Status.CLOSED, closed_at=at)


def test_notes_are_kept_while_the_booking_is_open() -> None:
    booking = evidence_booking()
    note_on(booking)
    assert retention.sweep(timezone.now() + dt.timedelta(days=3650)) == 0
    assert StaffNote.objects.get().delete_after is None


def test_notes_go_the_evidence_period_after_the_booking_closes() -> None:
    booking = evidence_booking()
    note = note_on(booking)
    closed_at = timezone.now()
    close(booking, closed_at)

    assert retention.sweep(closed_at + EVIDENCE_DAYS - dt.timedelta(minutes=1)) == 0
    assert StaffNote.objects.get().delete_after == closed_at + EVIDENCE_DAYS
    assert retention.sweep(closed_at + EVIDENCE_DAYS) == 1
    assert not StaffNote.objects.exists()

    event = StaffAuditEvent.objects.get(type=StaffAction.NOTE_PURGED)
    assert event.actor is None
    assert event.actor_kind == ActorKind.SYSTEM
    # The note alone: no booking, no `restricted` (review R1-2).
    assert (event.object_type, event.object_id) == ("staff.staffnote", str(note.pk))
    assert event.data == {}


def test_a_dispute_holds_the_notes_until_resolved_plus_the_hold_period() -> None:
    booking = evidence_booking(Booking.Status.PAYMENT_REQUESTED)
    note_on(booking)
    with pytest.MonkeyPatch.context() as patch:
        # Only the staff note hook is under test; the task photos are held as before.
        patch.setattr(state_machine, "place_legal_hold", lambda uploads: None)
        patch.setattr(state_machine, "lift_legal_hold", lambda uploads, resolved_at: None)
        state_machine._legal_hold(
            booking, Booking.Status.PAYMENT_REQUESTED, Booking.Status.DISPUTED, timezone.now()
        )
        assert StaffNote.objects.get().legal_hold

        Booking.objects.filter(pk=booking.pk).update(status=Booking.Status.DISPUTED)
        booking.refresh_from_db()
        assert note_on(booking).legal_hold  # added during the dispute: held too

        resolved_at = timezone.now()
        state_machine._legal_hold(
            booking, Booking.Status.DISPUTED, Booking.Status.RELEASED, resolved_at
        )
    close(booking, resolved_at)
    until = resolved_at.replace(year=resolved_at.year + settings.UPLOAD_LEGAL_HOLD_RETENTION_YEARS)
    for note in StaffNote.objects.all():
        assert not note.legal_hold
        assert note.delete_after == until
    # The booking's close doesn't shorten a lifted hold's date.
    assert retention.sweep(resolved_at + EVIDENCE_DAYS + dt.timedelta(days=1)) == 0
    assert retention.sweep(until) == 2


def test_held_notes_are_never_swept() -> None:
    booking = evidence_booking()
    note_on(booking)
    close(booking, timezone.now() - dt.timedelta(days=3650))
    retention.place_legal_hold("bookings.booking", str(booking.pk))
    assert retention.sweep() == 0
    assert StaffNote.objects.exists()


def test_account_deletion_redacts_unheld_notes_about_the_person() -> None:
    booking = evidence_booking()
    other = evidence_booking()
    note_on(booking)
    note_on(other)
    redacted, held = retention.redact_notes_about(booking.poster_id)
    assert (redacted, held) == (1, 0)

    mine = StaffNote.objects.get(object_id=str(booking.pk))
    assert mine.body == ""
    assert mine.redacted_at is not None
    assert StaffNote.objects.get(object_id=str(other.pk)).body == BODY
    event = StaffAuditEvent.objects.get(type=StaffAction.NOTE_REDACTED)
    assert event.actor_kind == ActorKind.SYSTEM
    assert (event.object_type, event.object_id) == ("staff.staffnote", str(mine.pk))
    assert event.data == {}

    client = Client()
    client.force_login(make_staff(Role.TRUST))
    html = client.get(reverse("admin:bookings_booking_change", args=[booking.pk])).content
    assert BODY.encode() not in html


def test_account_deletion_keeps_held_notes() -> None:
    booking = evidence_booking()
    note_on(booking)
    retention.place_legal_hold("bookings.booking", str(booking.pk))
    assert retention.redact_notes_about(booking.helper_id) == (0, 1)
    assert StaffNote.objects.get().body == BODY


def test_the_admin_has_no_path_that_changes_or_deletes_notes() -> None:
    from django.contrib import admin

    assert StaffNote not in admin.site._registry  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises(ValueError):
        StaffNote.objects.all().update(legal_hold=False)
