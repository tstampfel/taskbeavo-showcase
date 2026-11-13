import pytest
from django.contrib.auth.models import Group
from django.test import Client
from django.urls import reverse

from accounts.tests.factories import make_user
from staff.models import StaffAction, StaffAuditEvent
from staff.roles import Role
from staff.tests.factories import make_staff

pytestmark = pytest.mark.django_db


def test_only_support_and_trust_get_the_user_admin() -> None:
    def has_view_user(role: Role) -> bool:
        return (
            Group.objects.get(name=role)
            .permissions.filter(content_type__app_label="accounts", codename="view_user")
            .exists()
        )

    assert has_view_user(Role.SUPPORT)
    assert has_view_user(Role.TRUST)
    assert not has_view_user(Role.FINANCE)


def test_finance_cannot_open_a_user_page() -> None:
    member = make_user("member")
    client = Client()
    client.force_login(make_staff(Role.FINANCE))
    assert client.get(reverse("admin:accounts_user_change", args=[member.pk])).status_code == 403
    assert client.get(reverse("admin:accounts_user_changelist")).status_code == 403
    assert not StaffAuditEvent.objects.filter(type=StaffAction.VIEW_USER).exists()


@pytest.mark.parametrize("role", [Role.SUPPORT, Role.TRUST])
def test_every_view_of_a_user_page_is_audited_with_ids_only(role: Role) -> None:
    member = make_user("member")
    staff = make_staff(role)
    client = Client()
    client.force_login(staff)
    url = reverse("admin:accounts_user_change", args=[member.pk])
    assert client.get(url).status_code == 200
    assert client.get(url).status_code == 200

    events = StaffAuditEvent.objects.filter(type=StaffAction.VIEW_USER)
    assert events.count() == 2
    for event in events:
        assert event.actor == staff
        assert (event.object_type, event.object_id) == ("accounts.user", str(member.pk))
        assert event.data == {}


def test_nobody_can_set_the_account_status_in_the_admin() -> None:
    """Suspending and lifting come with the trust-safety flow and its statement of reasons;
    until then even a break-glass superuser can't (review BEAV-77 R1-3)."""
    member = make_user("member")
    client = Client()
    client.force_login(make_staff(is_superuser=True))
    url = reverse("admin:accounts_user_change", args=[member.pk])

    response = client.get(url)

    assert response.status_code == 200
    form = response.context["adminform"].form
    assert "status" not in form.fields
    assert "status" in response.context["adminform"].readonly_fields
