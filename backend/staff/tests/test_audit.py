import pytest
from django.contrib import admin
from django.contrib.auth.models import Group
from django.test import Client
from django.urls import reverse

from accounts.models import Session
from accounts.tests.factories import make_user, sign_in
from core.models import AuditEventError
from staff import audit
from staff.models import StaffAction, StaffAuditEvent
from staff.roles import Role
from staff.sites import AuditedModelAdmin, StaffAdminSite
from staff.tests.factories import make_staff

pytestmark = pytest.mark.django_db


def superuser_client() -> tuple[Client, object]:
    user = make_staff(is_superuser=True)
    client = Client()
    client.force_login(user)
    return client, user


def test_the_default_site_audits_every_registered_admin() -> None:
    assert isinstance(admin.site, StaffAdminSite)
    assert admin.site._registry  # pyright: ignore[reportAttributeAccessIssue]
    for model_admin in admin.site._registry.values():  # pyright: ignore[reportAttributeAccessIssue]
        assert isinstance(model_admin, AuditedModelAdmin)


def test_adding_changing_and_deleting_through_the_admin_is_audited() -> None:
    client, user = superuser_client()
    response = client.post(reverse("admin:auth_group_add"), {"name": "night-shift"})
    assert response.status_code == 302
    group = Group.objects.get(name="night-shift")
    client.post(reverse("admin:auth_group_change", args=[group.pk]), {"name": "late-shift"})
    client.post(reverse("admin:auth_group_delete", args=[group.pk]), {"post": "yes"})

    events = StaffAuditEvent.objects.filter(object_type="auth.group", object_id=str(group.pk))
    assert list(events.values_list("type", flat=True)) == [
        StaffAction.ADMIN_ADD,
        StaffAction.ADMIN_CHANGE,
        StaffAction.ADMIN_DELETE,
    ]
    change = events.get(type=StaffAction.ADMIN_CHANGE)
    assert change.data == {"fields": ["name"]}
    assert all(event.actor == user for event in events)


def test_admin_actions_are_audited_per_object() -> None:
    client, _ = superuser_client()
    member = make_user("member")
    sign_in(member)
    session = Session.objects.get(user=member)
    client.post(
        reverse("admin:accounts_session_changelist"),
        {"action": "revoke", "_selected_action": [str(session.pk)]},
    )
    event = StaffAuditEvent.objects.get(type=StaffAction.ADMIN_ACTION)
    assert event.object_type == "accounts.session"
    assert event.object_id == str(session.pk)
    assert event.data == {"action": "revoke"}


def test_audit_rows_are_append_only_and_refuse_personal_data() -> None:
    staff = make_staff()
    event = audit.record(staff, StaffAction.VIEW_BOOKING_EVIDENCE, ("bookings.booking", "1"))
    with pytest.raises(AuditEventError):
        event.save()
    with pytest.raises(AuditEventError):
        event.delete()
    with pytest.raises(ValueError):
        audit.record(staff, StaffAction.ADD_NOTE, ("bookings.booking", "1"), address="x")
    with pytest.raises(ValueError):
        audit.record(staff, StaffAction.ADD_NOTE, ("bookings.booking", "1"), who="a@example.com")


def test_role_groups_cannot_read_the_audit_log() -> None:
    for role in Role:
        client = Client()
        client.force_login(make_staff(role))
        response = client.get(reverse("admin:staff_staffauditevent_changelist"))
        assert response.status_code == 403


def test_an_action_on_every_row_audits_every_object() -> None:
    client, _ = superuser_client()
    sessions = []
    for name in ("first", "second"):
        member = make_user(name)
        sign_in(member)
        sessions.append(Session.objects.get(user=member))
    client.post(
        reverse("admin:accounts_session_changelist"),
        # "Select all": Django applies the action to the whole filtered list.
        {"action": "revoke", "select_across": "1", "_selected_action": [str(sessions[0].pk)]},
    )
    audited = set(
        StaffAuditEvent.objects.filter(type=StaffAction.ADMIN_ACTION).values_list(
            "object_id", flat=True
        )
    )
    assert audited == {str(session.pk) for session in sessions}


def test_list_pages_refuse_lookups_on_fields_they_do_not_filter() -> None:
    client, _ = superuser_client()
    profiles = reverse("admin:helpers_helperprofile_changelist")
    assert client.get(profiles, {"legal_last_name__startswith": "K"}).status_code == 400
    assert client.get(profiles, {"birth_date__year": "1950"}).status_code == 400
    assert client.get(profiles, {"user__email__startswith": "a"}).status_code == 400
    assert client.get(profiles, {"declared_status__exact": "PENSIONER"}).status_code == 200
    bookings = reverse("admin:bookings_booking_changelist")
    assert client.get(bookings, {"status__exact": "FUNDS_HELD"}).status_code == 200
    assert client.get(bookings, {"created_at__year": "2026"}).status_code == 200
    assert client.get(bookings, {"payment_intent_id__startswith": "pi_"}).status_code == 400
