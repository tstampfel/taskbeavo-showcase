import datetime as dt
from typing import Any

import pytest
from django.contrib.auth.models import Group, Permission
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from bookings.models import Booking, BookingEvent, NoFaultRequest
from core.models import ActorKind
from staff.models import StaffAction, StaffAuditEvent, StaffNote
from staff.roles import Role
from staff.tests.factories import ACCESS, ADDRESS, CONTACT_PHONE, evidence_booking, make_staff
from uploads.models import Upload

pytestmark = pytest.mark.django_db

RESTRICTED_BODY = "Mama seemed confused and had a bruise on her arm."
NORMAL_BODY = "Poster called about the hedge."


def url(booking: Booking) -> str:
    return reverse("admin:bookings_booking_change", args=[booking.pk])


def note_url(booking: Booking) -> str:
    return reverse("admin:bookings_booking_add_note", args=[booking.pk])


def page(user: Any, booking: Booking) -> str:
    client = Client()
    client.force_login(user)
    response = client.get(url(booking))
    assert response.status_code == 200
    # admin_view's never_cache: personal data is never kept by a browser or proxy.
    assert "no-store" in response["Cache-Control"]
    return response.content.decode()


def add_notes(booking: Booking) -> None:
    trust = make_staff(Role.TRUST)
    StaffNote.objects.create(
        object_type="bookings.booking", object_id=str(booking.pk), author=trust, body=NORMAL_BODY
    )
    StaffNote.objects.create(
        object_type="bookings.booking",
        object_id=str(booking.pk),
        author=trust,
        body=RESTRICTED_BODY,
        restricted=True,
    )


def test_trust_sees_the_exact_place_contact_phone_photos_and_restricted_notes() -> None:
    booking = evidence_booking()
    add_notes(booking)
    html = page(make_staff(Role.TRUST), booking)
    assert ADDRESS in html
    assert ACCESS in html
    assert CONTACT_PHONE in html
    assert "fake-r2.invalid" in html
    assert RESTRICTED_BODY in html
    assert NORMAL_BODY in html
    assert "pi_test_evidence" not in html


def test_support_sees_the_place_and_photos_but_not_the_phone_or_restricted_notes() -> None:
    booking = evidence_booking()
    add_notes(booking)
    html = page(make_staff(Role.SUPPORT), booking)
    assert ADDRESS in html
    assert ACCESS in html
    assert CONTACT_PHONE not in html
    assert "•••• 678" in html
    assert "fake-r2.invalid" in html
    assert "pi_test_evidence" in html
    assert NORMAL_BODY in html
    assert RESTRICTED_BODY not in html
    # Not even a hint that a restricted note exists.
    assert 'class="restricted"' not in html


def test_finance_sees_payments_but_not_the_place_photos_chat_or_restricted_notes() -> None:
    booking = evidence_booking()
    add_notes(booking)
    html = page(make_staff(Role.FINANCE), booking)
    assert ADDRESS not in html
    assert ACCESS not in html
    assert "Mama Kata" not in html
    assert CONTACT_PHONE not in html
    assert "fake-r2.invalid" not in html
    assert "pi_test_evidence" in html
    assert RESTRICTED_BODY not in html
    assert NORMAL_BODY in html


def test_superuser_outside_trust_sees_neither_restricted_notes_nor_the_full_phone() -> None:
    booking = evidence_booking()
    add_notes(booking)
    html = page(make_staff(is_superuser=True), booking)
    assert NORMAL_BODY in html
    assert RESTRICTED_BODY not in html
    assert CONTACT_PHONE not in html
    assert "•••• 678" in html


def test_every_view_is_audited_without_personal_data() -> None:
    booking = evidence_booking()
    trust = make_staff(Role.TRUST)
    page(trust, booking)
    page(trust, booking)
    events = StaffAuditEvent.objects.filter(
        type=StaffAction.VIEW_BOOKING_EVIDENCE, object_id=str(booking.pk)
    )
    assert events.count() == 2
    event = events.first()
    assert event is not None
    assert event.actor == trust
    assert event.object_type == "bookings.booking"
    assert "view_contact_phone" in event.data["sections"]
    logged = str(event.data)
    for secret in (ADDRESS, ACCESS, CONTACT_PHONE, "Mama Kata"):
        assert secret not in logged


def test_viewing_changes_neither_the_booking_nor_photo_retention() -> None:
    booking = evidence_booking()
    before = list(Upload.objects.values("pk", "delete_after", "legal_hold", "public_key"))
    page(make_staff(Role.TRUST), booking)
    assert list(Upload.objects.values("pk", "delete_after", "legal_hold", "public_key")) == before
    booking.refresh_from_db()
    assert booking.status == Booking.Status.FUNDS_HELD
    assert not BookingEvent.objects.filter(booking=booking, actor_kind="ADMIN").exists()


def test_non_staff_and_roleless_staff_are_refused() -> None:
    booking = evidence_booking()
    client = Client()
    client.force_login(make_staff(Role.TRUST, is_staff=False))
    assert client.get(url(booking)).status_code == 302  # to the admin login
    client.force_login(make_staff())
    assert client.get(url(booking)).status_code == 403
    assert not StaffAuditEvent.objects.exists()


def test_trust_adds_a_restricted_note() -> None:
    booking = evidence_booking()
    trust = make_staff(Role.TRUST)
    client = Client()
    client.force_login(trust)
    response = client.post(note_url(booking), {"body": RESTRICTED_BODY, "restricted": "on"})
    assert response.status_code == 302
    note = StaffNote.objects.get()
    assert note.restricted
    assert note.author == trust
    event = StaffAuditEvent.objects.get(type=StaffAction.ADD_NOTE)
    # The note alone, not its booking or `restricted`: the log never hints that a
    # restricted note exists (review R1-2).
    assert (event.object_type, event.object_id) == ("staff.staffnote", str(note.pk))
    assert event.data == {}
    assert not StaffAuditEvent.objects.filter(
        object_type="bookings.booking", type=StaffAction.ADD_NOTE
    ).exists()


def test_support_cannot_write_a_restricted_note() -> None:
    booking = evidence_booking()
    client = Client()
    client.force_login(make_staff(Role.SUPPORT))
    response = client.post(note_url(booking), {"body": RESTRICTED_BODY, "restricted": "on"})
    assert response.status_code == 403
    assert not StaffNote.objects.exists()


def test_support_adds_a_normal_note_and_the_form_offers_no_restricted_box() -> None:
    booking = evidence_booking()
    support = make_staff(Role.SUPPORT)
    client = Client()
    client.force_login(support)
    assert 'name="restricted"' not in client.get(url(booking)).content.decode()
    assert client.post(note_url(booking), {"body": NORMAL_BODY}).status_code == 302
    assert StaffNote.objects.get().restricted is False


def test_note_text_is_encrypted_at_rest() -> None:
    booking = evidence_booking()
    add_notes(booking)
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SELECT body FROM staff_notes")
        stored = [row[0] for row in cursor.fetchall()]
    assert RESTRICTED_BODY not in stored
    assert NORMAL_BODY not in stored


def test_staff_notes_are_append_only() -> None:
    booking = evidence_booking()
    add_notes(booking)
    note = StaffNote.objects.first()
    assert note is not None
    with pytest.raises(ValueError):
        note.save()
    with pytest.raises(ValueError):
        note.delete()
    with pytest.raises(ValueError):
        StaffNote.objects.all().delete()


def test_the_booking_list_shows_no_personal_data() -> None:
    booking = evidence_booking()
    client = Client()
    client.force_login(make_staff(Role.SUPPORT))
    html = client.get(reverse("admin:bookings_booking_changelist")).content.decode()
    assert str(booking.pk) in html
    assert ADDRESS not in html
    assert not StaffAuditEvent.objects.exists()


def test_finance_sees_user_ids_without_links_to_user_pages() -> None:
    booking = evidence_booking()
    html = page(make_staff(Role.FINANCE), booking)
    assert reverse("admin:accounts_user_change", args=[booking.poster_id]) not in html
    html = page(make_staff(Role.SUPPORT), booking)
    assert reverse("admin:accounts_user_change", args=[booking.poster_id]) in html


# --- The specific no-fault or illness reason: view_no_fault_detail only (BEAV-123). ---


def no_fault_detail() -> Permission:
    return Permission.objects.get(content_type__app_label="staff", codename="view_no_fault_detail")


def cancelled_booking(no_fault_reason: str, helper_reason: str) -> Booking:
    booking = evidence_booking(Booking.Status.CANCEL_REQUESTED)
    now = timezone.now()
    NoFaultRequest.objects.create(
        booking=booking,
        reason=no_fault_reason,
        requested_by=ActorKind.POSTER,
        requested_at=now,
        respond_by=now + dt.timedelta(hours=48),
    )
    # A helper cancel, as its transition records it.
    BookingEvent.objects.create(
        booking=booking,
        type="status_changed",
        actor_kind=ActorKind.HELPER,
        data={"reason": helper_reason, "case": "HELPER_CANCEL"},
    )
    return booking


def hospital_booking() -> Booking:
    return cancelled_booking("HOSPITALISATION", "ILL")


@pytest.mark.parametrize("role", [Role.SUPPORT, Role.FINANCE, Role.TRUST])
def test_without_the_permission_staff_see_only_the_generic_label(role: Role) -> None:
    html = page(make_staff(role), hospital_booking())

    assert "PERSONAL_EMERGENCY" in html
    assert "reason: PERSONAL_REASON" in html
    assert "HOSPITALISATION" not in html
    assert "reason: ILL" not in html


@pytest.mark.parametrize("reason", ["BEREAVEMENT", "EMERGENCY"])
def test_the_generic_no_fault_label_cant_be_told_apart(reason: str) -> None:
    """Hospitalisation, bereavement and a declared emergency all read the same."""
    hospital = page(make_staff(Role.SUPPORT), cancelled_booking("HOSPITALISATION", "ILL"))
    other = page(make_staff(Role.SUPPORT), cancelled_booking(reason, "ILL"))

    assert reason not in other.replace("PERSONAL_EMERGENCY", "")
    assert hospital.count("PERSONAL_EMERGENCY") == other.count("PERSONAL_EMERGENCY") == 1


def test_a_helpers_illness_reads_like_any_other_personal_reason() -> None:
    ill = page(make_staff(Role.SUPPORT), cancelled_booking("WEATHER", "ILL"))
    other = page(make_staff(Role.SUPPORT), cancelled_booking("WEATHER", "OTHER"))
    double_booked = page(make_staff(Role.SUPPORT), cancelled_booking("WEATHER", "DOUBLE_BOOKED"))

    assert "reason: PERSONAL_REASON" in ill
    assert "reason: PERSONAL_REASON" in other
    assert "reason: ILL" not in ill and "reason: OTHER" not in other
    # Not a personal reason: shown as it is.
    assert "reason: DOUBLE_BOOKED" in double_booked
    assert "WEATHER" in ill


def test_staff_granted_the_permission_see_the_specific_reason() -> None:
    support = make_staff(Role.SUPPORT)
    support.user_permissions.add(no_fault_detail())

    html = page(support, hospital_booking())

    assert "HOSPITALISATION" in html
    assert "reason: ILL" in html


def test_a_group_of_the_founders_can_carry_the_permission() -> None:
    deciders = Group.objects.create(name="no-fault deciders")
    deciders.permissions.add(no_fault_detail())
    support = make_staff(Role.SUPPORT)
    support.groups.add(deciders)

    assert "HOSPITALISATION" in page(support, hospital_booking())


def test_a_superuser_needs_the_permission_granted_too() -> None:
    html = page(make_staff(is_superuser=True), hospital_booking())

    assert "HOSPITALISATION" not in html
    assert "PERSONAL_EMERGENCY" in html


def test_masking_the_reason_never_changes_the_stored_event() -> None:
    booking = hospital_booking()

    page(make_staff(Role.SUPPORT), booking)

    assert BookingEvent.objects.get(booking=booking, type="status_changed").data == {
        "reason": "ILL",
        "case": "HELPER_CANCEL",
    }
