"""Support and finance correct a trader's registered business address in the admin
(staff migration 0004, BEAV-110); nothing else on the helper profile changes, the edit is
audited with field names only, and trust has no access."""

import pytest
from django.test import Client
from django.urls import reverse

from helpers.models import HelperProfile
from helpers.tests.business import ADDRESS
from offers.tests.factories import make_helper
from staff.models import StaffAction, StaffAuditEvent
from staff.roles import Role
from staff.tests.factories import make_staff

pytestmark = pytest.mark.django_db


def trader() -> HelperProfile:
    profile = HelperProfile.objects.get(user=make_helper("marko"))
    HelperProfile.objects.filter(pk=profile.pk).update(
        declared_status=HelperProfile.Status.BUSINESS,
        business_kind=HelperProfile.BusinessKind.OBRT,
        business_name="Obrt Marko",
        business_oib=profile.oib,
    )
    return HelperProfile.objects.get(pk=profile.pk)


def save(role: Role, profile: HelperProfile, data: dict[str, str]) -> int:
    client = Client()
    client.force_login(make_staff(role))
    url = reverse("admin:helpers_helperprofile_change", args=[profile.pk])
    return client.post(url, {**data, "_save": "Save"}).status_code


@pytest.mark.parametrize("role", [Role.SUPPORT, Role.FINANCE])
def test_support_and_finance_set_the_business_address_and_it_is_audited(role: Role) -> None:
    profile = trader()
    assert profile.business_address == ""

    sneaky = {"business_name": "Renamed", "declared_status": "PRIVATE", "categories": "[]"}
    assert save(role, profile, {**ADDRESS, **sneaky}) == 302

    profile.refresh_from_db()
    assert profile.business_address == "Obala Stjepana Radića 12, 20000 Dubrovnik, HR"
    # Every other field is read-only.
    assert (profile.business_name, profile.declared_status) == (
        "Obrt Marko",
        HelperProfile.Status.BUSINESS,
    )
    [event] = StaffAuditEvent.objects.filter(type=StaffAction.ADMIN_CHANGE)
    assert event.object_type == "helpers.helperprofile"
    assert event.object_id == str(profile.pk)
    assert sorted(event.data["fields"]) == sorted(ADDRESS)
    assert "Obala" not in str(event.data)


def test_trust_cannot_open_or_change_the_helper_profile() -> None:
    profile = trader()

    assert save(Role.TRUST, profile, ADDRESS) == 403

    profile.refresh_from_db()
    assert profile.business_address == ""
    assert not StaffAuditEvent.objects.filter(type=StaffAction.ADMIN_CHANGE).exists()


def test_support_sees_tax_ids_masked_and_cannot_reveal_them() -> None:
    profile = trader()
    client = Client()
    client.force_login(make_staff(Role.SUPPORT))

    html = client.get(
        reverse("admin:helpers_helperprofile_change", args=[profile.pk])
    ).content.decode()

    assert profile.oib not in html
    assert profile.iban not in html
    reveal = reverse("admin:helpers_helperprofile_reveal", args=[profile.pk])
    assert client.post(reveal).status_code == 403
