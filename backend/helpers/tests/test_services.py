"""`require_can_send_offers`, the guard `sendOffer` (BEAV-85) calls first."""

import dataclasses
import datetime as dt
import io
from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.management import CommandError, call_command
from django.db import IntegrityError
from django.utils import timezone

from accounts.models import User
from accounts.tests.factories import make_user
from areas.models import ServiceArea
from core.categories import TaskCategory
from core.crypto import pii_digest
from core.errors import ApiError
from core.synthetic_identifiers import synthetic_oib
from helpers import services
from helpers.countries import EU_MEMBER_STATES
from helpers.iban import EU_IBAN_LENGTHS
from helpers.models import BusinessVerification, HelperProfile
from helpers.tests.business import give_business_address

pytestmark = pytest.mark.django_db


def complete_changes() -> services.ProfileChanges:
    return services.ProfileChanges(
        status=HelperProfile.Status.PENSIONER,
        legal_first_name="Marija",
        legal_last_name="Kovač",
        birth_date=dt.date(1955, 6, 1),
        address=services.Address("Put Republike 3", "20000", "Dubrovnik", "hr"),
        oib="00000000698",
        iban="HR0600000000000000160",
        residence_country="hr",
        service_area_ids=[str(ServiceArea.objects.get(slug="dubrovnik").pk)],
        categories=[TaskCategory.CHECK_IN, TaskCategory.CHECK_IN],
    )


def code_of(raised: pytest.ExceptionInfo[ApiError]) -> str:
    return raised.value.code.name


def test_a_complete_helper_passes() -> None:
    marija = make_user("marija", phone_verified_at=timezone.now())
    profile = services.update_profile(marija, complete_changes())

    assert services.require_can_send_offers(marija) == profile
    assert services.can_send_offers(marija)
    # Duplicates are dropped and codes normalised.
    assert profile.categories == ["CHECK_IN"]
    assert (profile.address_country, profile.residence_country) == ("HR", "HR")


def test_signed_out_is_unauthenticated() -> None:
    with pytest.raises(ApiError) as raised:
        services.require_can_send_offers(AnonymousUser())
    assert code_of(raised) == "UNAUTHENTICATED"


def test_an_unverified_phone_comes_first() -> None:
    marija = make_user("marija")
    services.update_profile(marija, complete_changes())

    with pytest.raises(ApiError) as raised:
        services.require_can_send_offers(marija)
    assert code_of(raised) == "PHONE_NOT_VERIFIED"


def test_no_profile_or_an_incomplete_one_is_refused() -> None:
    marija = make_user("marija", phone_verified_at=timezone.now())
    with pytest.raises(ApiError) as raised:
        services.require_can_send_offers(marija)
    assert code_of(raised) == "HELPER_PROFILE_INCOMPLETE"
    assert services.missing_requirements(marija, None)[0] == services.Requirement.STATUS
    assert services.Requirement.PHONE_VERIFIED not in services.missing_requirements(marija, None)

    services.update_profile(marija, services.ProfileChanges(status=HelperProfile.Status.STUDENT))
    with pytest.raises(ApiError) as raised:
        services.require_can_send_offers(marija)
    assert code_of(raised) == "HELPER_PROFILE_INCOMPLETE"


def test_recompute_pii_digests_covers_helper_oibs(settings: Any) -> None:
    marija = make_user("marija")
    services.update_profile(marija, services.ProfileChanges(oib="00000000698"))

    settings.PII_DIGEST_KEY = "a-new-digest-key-not-secret"
    out = io.StringIO()
    call_command("recompute_pii_digests", stdout=out)

    assert HelperProfile.objects.get().oib_digest == pii_digest("oib", "00000000698")
    assert "1 OIB digests recomputed" in out.getvalue()
    assert "0698" not in out.getvalue()


def test_an_integrity_error_not_about_the_oib_is_not_reported_as_oib_in_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(profile: HelperProfile, changes: services.ProfileChanges) -> None:
        raise IntegrityError("some other constraint")

    monkeypatch.setattr(services, "_apply", broken)

    with pytest.raises(IntegrityError):
        services.update_profile(make_user("marija"), services.ProfileChanges(oib="00000000698"))


def test_a_concurrent_duplicate_oib_is_reported_as_oib_in_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services.update_profile(make_user("ivo"), services.ProfileChanges(oib="00000000698"))

    def racing(profile: HelperProfile, changes: services.ProfileChanges) -> None:
        # As if Ivo's save landed after this helper's uniqueness check passed.
        profile.oib = "00000000698"
        profile.oib_digest = pii_digest("oib", "00000000698")

    monkeypatch.setattr(services, "_apply", racing)

    with pytest.raises(ApiError) as raised:
        services.update_profile(make_user("marija"), services.ProfileChanges(oib="00000000698"))
    assert code_of(raised) == "OIB_IN_USE"


def test_residence_country_uses_the_one_allowed_set(monkeypatch: pytest.MonkeyPatch) -> None:
    marija = make_user("marija")
    monkeypatch.setattr(services, "RESIDENCE_COUNTRIES", frozenset({"HR"}))

    with pytest.raises(ApiError) as raised:
        services.update_profile(marija, services.ProfileChanges(residence_country="DE"))
    assert code_of(raised) == "RESIDENCE_COUNTRY_NOT_ALLOWED"
    profile = services.update_profile(marija, services.ProfileChanges(residence_country="hr"))
    assert profile.residence_country == "HR"


def test_the_residence_countries_are_the_eu_member_states() -> None:
    assert services.RESIDENCE_COUNTRIES == EU_MEMBER_STATES == frozenset(EU_IBAN_LENGTHS)


COMPANY_OIB = synthetic_oib(94)


def company_helper() -> tuple[User, HelperProfile]:
    marija = make_user("marija", phone_verified_at=timezone.now())
    company = services.Business(
        kind=HelperProfile.BusinessKind.COMPANY,
        name="Beavo d.o.o.",
        oib=COMPANY_OIB,
        issues_fiscalized_invoices=True,
    )
    changes = dataclasses.replace(
        complete_changes(), status=HelperProfile.Status.BUSINESS, business=company
    )
    services.update_profile(marija, changes)
    give_business_address(marija)
    return marija, HelperProfile.objects.get(user=marija)


def verify(profile_id: int, oib: str = COMPANY_OIB, name: str = "Beavo d.o.o.") -> HelperProfile:
    return services.verify_business(
        profile_id, business_oib=oib, business_name=name, by=BusinessVerification.COURT_REGISTER
    )


def test_verify_business_is_only_for_a_company_with_details() -> None:
    marija = make_user("marija")
    profile = services.update_profile(marija, complete_changes())
    with pytest.raises(ValueError):
        verify(profile.pk)


def test_verify_business_verifies_exactly_the_checked_details() -> None:
    marija, profile = company_helper()

    verified = verify(profile.pk, name="  Beavo   d.o.o. ")

    assert verified.business_verified_by == "COURT_REGISTER"
    assert services.can_send_offers(marija)


@pytest.mark.parametrize(
    ("oib", "name"),
    [("00000000001", "Beavo d.o.o."), (COMPANY_OIB, "Beavo j.d.o.o.")],
)
def test_verify_business_refuses_details_other_than_the_checked_ones(oib: str, name: str) -> None:
    _marija, profile = company_helper()

    with pytest.raises(ValueError) as raised:
        verify(profile.pk, oib=oib, name=name)

    assert "Beavo" not in str(raised.value) and oib not in str(raised.value)
    assert HelperProfile.objects.get().business_verified_at is None


def test_details_changed_between_the_check_and_the_call_stay_unverified() -> None:
    marija, profile = company_helper()
    # Staff checked COMPANY_OIB in the court register; then the helper swaps the OIB.
    checked_oib, checked_name = profile.business_oib, profile.business_name
    swapped = services.Business(
        kind=HelperProfile.BusinessKind.COMPANY,
        name="Beavo d.o.o.",
        oib="00000000001",
        issues_fiscalized_invoices=True,
    )
    services.update_profile(marija, services.ProfileChanges(business=swapped))

    with pytest.raises(ValueError):
        services.verify_business(
            profile.pk,
            business_oib=checked_oib,
            business_name=checked_name,
            by=BusinessVerification.STAFF,
        )

    assert HelperProfile.objects.get().business_verified_at is None
    assert not services.can_send_offers(marija)


def test_staff_verify_a_company_with_the_management_command() -> None:
    marija, _profile = company_helper()
    assert not services.can_send_offers(marija)

    out = io.StringIO()
    call_command(
        "verify_helper_business",
        "--user-id",
        str(marija.pk),
        "--business-oib",
        COMPANY_OIB,
        "--business-name",
        "Beavo d.o.o.",
        stdout=out,
    )

    profile = HelperProfile.objects.get()
    assert profile.business_verified_by == "STAFF"
    assert services.can_send_offers(marija)
    assert f"verified Beavo d.o.o. (OIB {COMPANY_OIB})" in out.getvalue()
    # Never the helper's personal details.
    assert "00000000698" not in out.getvalue() and "Kovač" not in out.getvalue()


def test_the_command_refuses_a_user_without_a_company() -> None:
    marija = make_user("marija")
    args = ["--business-oib", COMPANY_OIB, "--business-name", "Beavo d.o.o."]
    with pytest.raises(CommandError):
        call_command("verify_helper_business", "--user-id", str(marija.pk), *args)
    services.update_profile(marija, complete_changes())
    with pytest.raises(CommandError):
        call_command("verify_helper_business", "--user-id", str(marija.pk), *args)


def test_the_command_refuses_a_company_whose_details_changed() -> None:
    marija, _profile = company_helper()
    args = ["--business-oib", "00000000001", "--business-name", "Beavo d.o.o."]

    with pytest.raises(CommandError):
        call_command("verify_helper_business", "--user-id", str(marija.pk), *args)

    assert HelperProfile.objects.get().business_verified_at is None
