import pytest
from django.test import Client
from django.urls import reverse

from core.synthetic_identifiers import synthetic_iban
from helpers.models import HelperProfile
from offers.tests.factories import make_helper
from staff.models import StaffAction, StaffAuditEvent
from staff.roles import Role
from staff.tests.factories import make_staff

pytestmark = pytest.mark.django_db

IBAN = synthetic_iban(160)


def profile() -> HelperProfile:
    return HelperProfile.objects.get(user=make_helper("marko"))


def client_for(user: object) -> Client:
    client = Client()
    client.force_login(user)  # pyright: ignore[reportArgumentType]
    return client


def test_the_profile_shows_tax_ids_masked() -> None:
    helper = profile()
    html = (
        client_for(make_staff(Role.FINANCE))
        .get(reverse("admin:helpers_helperprofile_change", args=[helper.pk]))
        .content.decode()
    )
    assert IBAN not in html
    assert helper.oib not in html
    assert "•••• 0160" in html
    assert "Marko" not in html  # the legal name stays out


def test_finance_reveals_the_tax_ids_and_it_is_audited() -> None:
    helper = profile()
    finance = make_staff(Role.FINANCE)
    response = client_for(finance).post(
        reverse("admin:helpers_helperprofile_reveal", args=[helper.pk])
    )
    assert response.status_code == 200
    # admin_view's never_cache: personal data is never kept by a browser or proxy.
    assert "no-store" in response["Cache-Control"]
    html = response.content.decode()
    assert IBAN in html
    assert helper.oib in html
    event = StaffAuditEvent.objects.get(type=StaffAction.REVEAL_TAX_IDS)
    assert event.actor == finance
    assert event.object_type == "helpers.helperprofile"
    assert event.object_id == str(helper.pk)
    assert event.data == {"fields": ["oib", "iban"]}
    assert IBAN not in str(event.data)


@pytest.mark.parametrize("who", ["support", "trust", "superuser"])
def test_only_finance_can_reveal(who: str) -> None:
    helper = profile()
    user = make_staff(is_superuser=True) if who == "superuser" else make_staff(Role(who))
    response = client_for(user).post(
        reverse("admin:helpers_helperprofile_reveal", args=[helper.pk])
    )
    assert response.status_code == 403
    assert not StaffAuditEvent.objects.filter(type=StaffAction.REVEAL_TAX_IDS).exists()


def test_a_get_never_reveals() -> None:
    helper = profile()
    response = client_for(make_staff(Role.FINANCE)).get(
        reverse("admin:helpers_helperprofile_reveal", args=[helper.pk])
    )
    assert response.status_code == 405
