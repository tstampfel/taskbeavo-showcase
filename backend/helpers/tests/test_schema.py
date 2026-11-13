"""The helper profile over HTTP at /graphql (plan: "Becoming a helper", DAC7)."""

from types import SimpleNamespace
from typing import Any

import pytest
from django.core.cache import caches
from django.db import connection
from django.test import Client
from django.utils import timezone

from accounts.models import User
from accounts.tests.factories import make_user, sign_in
from accounts.tests.test_schema import error_codes, gql
from areas.models import ServiceArea, WaitlistEntry
from core.synthetic_identifiers import synthetic_iban, synthetic_oib
from helpers import services
from helpers.models import BusinessVerification, HelperProfile
from helpers.tests.business import give_business_address
from payments import connect
from payments.models import ConnectedAccount

pytestmark = pytest.mark.django_db

OIB = synthetic_oib(69)
OTHER_OIB = synthetic_oib(94)
IBAN = synthetic_iban(160)

FIELDS = """
id status isTrader businessKind businessName businessOib issuesFiscalizedInvoices
businessAddressStreet businessAddressPostalCode businessAddressCity businessAddressCountry
legalFirstName legalLastName birthDate addressStreet addressPostalCode addressCity
addressCountry businessVerified oibMasked ibanMasked vatIdMasked residenceCountry categories
serviceAreas { id name slug } missing canSendOffers createdAt updatedAt
"""
MINE = f"{{ myHelperProfile {{ {FIELDS} }} }}"
UPDATE = f"""
mutation($input: UpdateHelperProfileInput!) {{ updateHelperProfile(input: $input) {{ {FIELDS} }} }}
"""
PUBLIC = """
query($userId: ID!) {
  helperProfile(userId: $userId) {
    id isTrader businessName businessOib businessTown categories serviceAreas { id name }
  }
}
"""
JOIN_WAITLIST = """
mutation($input: JoinWaitlistInput!) { joinWaitlist(input: $input) { id city countryCode } }
"""


def dubrovnik() -> ServiceArea:
    return ServiceArea.objects.get(slug="dubrovnik")


def complete_input() -> dict[str, Any]:
    return {
        "status": "PRIVATE_INDIVIDUAL",
        "legalFirstName": "Luka",
        "legalLastName": "Horvat",
        "birthDate": "2002-03-14",
        "address": {
            "street": "Ulica Ante Starčevića 12",
            "postalCode": "20000",
            "city": "Dubrovnik",
            "country": "HR",
        },
        "oib": OIB,
        "iban": "hr06 0000 0000 0000 0016 0",
        "residenceCountry": "HR",
        "serviceAreaIds": [str(dubrovnik().pk)],
        "categories": ["GARDEN", "REPAIRS"],
    }


def verify_company(user: User) -> None:
    """Staff check the company's current name and OIB and verify exactly those."""
    profile = HelperProfile.objects.get(user=user)
    services.verify_business(
        profile.pk,
        business_oib=profile.business_oib,
        business_name=profile.business_name,
        by=BusinessVerification.STAFF,
    )


def verified_user(name: str = "luka") -> User:
    return make_user(name, phone_verified_at=timezone.now())


def save(client: Client, user: User, changes: dict[str, Any]) -> dict[str, Any]:
    return gql(client, UPDATE, {"input": changes}, token=sign_in(user).access_token)


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> None:
    caches["graphql_rate_limit"].clear()


def test_signed_out_callers_are_refused(client: Client) -> None:
    assert error_codes(gql(client, MINE)) == ["UNAUTHENTICATED"]
    assert error_codes(gql(client, UPDATE, {"input": complete_input()})) == ["UNAUTHENTICATED"]
    assert not HelperProfile.objects.exists()


def test_a_user_without_a_profile_gets_null(client: Client) -> None:
    body = gql(client, MINE, token=sign_in(make_user()).access_token)
    assert body == {"data": {"myHelperProfile": None}}


def test_a_complete_profile_can_send_offers_and_returns_the_full_object(client: Client) -> None:
    luka = verified_user()

    body = save(client, luka, complete_input())

    saved = body["data"]["updateHelperProfile"]
    profile = HelperProfile.objects.get()
    assert saved["id"] == str(profile.pk)
    assert profile.user == luka
    assert saved["canSendOffers"] is True
    assert saved["missing"] == []
    assert {k: saved[k] for k in ("status", "isTrader", "legalFirstName", "birthDate")} == {
        "status": "PRIVATE_INDIVIDUAL",
        "isTrader": False,
        "legalFirstName": "Luka",
        "birthDate": "2002-03-14",
    }
    assert (saved["addressPostalCode"], saved["addressCountry"]) == ("20000", "HR")
    assert saved["oibMasked"] == "•••••••0698"
    assert saved["ibanMasked"] == "HR•• •••• •••• •••• •016 0"
    assert saved["categories"] == ["GARDEN", "REPAIRS"]
    assert saved["serviceAreas"] == [
        {"id": str(dubrovnik().pk), "name": dubrovnik().name, "slug": "dubrovnik"}
    ]
    assert saved["createdAt"] and saved["updatedAt"]
    assert profile.iban == IBAN
    # The owner never gets the OIB or IBAN back in full.
    assert OIB not in str(body) and IBAN not in str(body)


def test_the_profile_is_filled_in_step_by_step(client: Client) -> None:
    luka = verified_user()
    token = sign_in(luka).access_token
    full = complete_input()

    first = gql(client, UPDATE, {"input": {"status": "STUDENT"}}, token=token)
    assert first["data"]["updateHelperProfile"]["canSendOffers"] is False
    assert "OIB" in first["data"]["updateHelperProfile"]["missing"]

    rest = {k: v for k, v in full.items() if k != "status"}
    last = gql(client, UPDATE, {"input": rest}, token=token)

    assert last["data"]["updateHelperProfile"]["status"] == "STUDENT"
    assert last["data"]["updateHelperProfile"]["canSendOffers"] is True
    mine = gql(client, MINE, token=token)["data"]["myHelperProfile"]
    assert mine == last["data"]["updateHelperProfile"]


@pytest.mark.parametrize(
    ("field", "requirement"),
    [
        ("status", "STATUS"),
        ("legalFirstName", "LEGAL_NAME"),
        ("legalLastName", "LEGAL_NAME"),
        ("birthDate", "BIRTH_DATE"),
        ("address", "ADDRESS"),
        ("oib", "OIB"),
        ("iban", "IBAN"),
        ("residenceCountry", "RESIDENCE_COUNTRY"),
        ("serviceAreaIds", "SERVICE_AREAS"),
        ("categories", "CATEGORIES"),
    ],
)
def test_can_send_offers_needs_every_field(client: Client, field: str, requirement: str) -> None:
    changes = {k: v for k, v in complete_input().items() if k != field}

    saved = save(client, verified_user(), changes)["data"]["updateHelperProfile"]

    assert saved["canSendOffers"] is False
    assert saved["missing"] == [requirement]


def test_can_send_offers_needs_a_verified_phone(client: Client) -> None:
    saved = save(client, make_user("luka"), complete_input())["data"]["updateHelperProfile"]
    assert saved["canSendOffers"] is False
    assert saved["missing"] == ["PHONE_VERIFIED"]


def test_empty_lists_clear_service_areas_and_categories(client: Client) -> None:
    luka = verified_user()
    save(client, luka, complete_input())

    saved = save(client, luka, {"serviceAreaIds": [], "categories": []})
    assert saved["data"]["updateHelperProfile"]["missing"] == ["SERVICE_AREAS", "CATEGORIES"]


def test_a_deactivated_service_area_no_longer_counts(client: Client) -> None:
    luka = verified_user()
    save(client, luka, complete_input())
    ServiceArea.objects.filter(slug="dubrovnik").update(active=False)

    mine = gql(client, MINE, token=sign_in(luka).access_token)["data"]["myHelperProfile"]
    assert mine["serviceAreas"] == []
    assert mine["missing"] == ["SERVICE_AREAS"]


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"oib": "00000000699"}, "INVALID_OIB"),
        ({"oib": "0000000069"}, "INVALID_OIB"),
        ({"oib": "abcdefghijk"}, "INVALID_OIB"),
        ({"iban": "HR0600000000000000161"}, "INVALID_IBAN"),
        ({"iban": "GB88000000000000000001"}, "INVALID_IBAN"),
        ({"vatId": "HR00000000699"}, "INVALID_VAT_ID"),
        ({"vatId": "X"}, "INVALID_VAT_ID"),
        ({"legalFirstName": "  "}, "INVALID_HELPER_DETAILS"),
        ({"legalLastName": "x" * 101}, "INVALID_HELPER_DETAILS"),
        ({"birthDate": "2999-01-01"}, "INVALID_HELPER_DETAILS"),
        ({"birthDate": "1880-01-01"}, "INVALID_HELPER_DETAILS"),
        ({"residenceCountry": "Croatia"}, "RESIDENCE_COUNTRY_NOT_ALLOWED"),
        ({"residenceCountry": "XX"}, "RESIDENCE_COUNTRY_NOT_ALLOWED"),
        ({"residenceCountry": "US"}, "RESIDENCE_COUNTRY_NOT_ALLOWED"),
        ({"residenceCountry": "CH"}, "RESIDENCE_COUNTRY_NOT_ALLOWED"),
        ({"residenceCountry": "EL"}, "RESIDENCE_COUNTRY_NOT_ALLOWED"),
        (
            {
                "address": {
                    "street": "Ilica 1",
                    "postalCode": "2000",
                    "city": "Zagreb",
                    "country": "HR",
                }
            },
            "INVALID_HELPER_DETAILS",
        ),
        (
            {"address": {"street": "", "postalCode": "10000", "city": "Zagreb", "country": "HR"}},
            "INVALID_HELPER_DETAILS",
        ),
        ({"serviceAreaIds": ["999999"]}, "SERVICE_AREA_NOT_FOUND"),
        ({"serviceAreaIds": ["dubrovnik"]}, "SERVICE_AREA_NOT_FOUND"),
    ],
)
def test_invalid_values_are_refused_and_nothing_is_saved(
    client: Client, change: dict[str, Any], code: str
) -> None:
    body = save(client, verified_user(), {**complete_input(), **change})

    assert error_codes(body) == [code]
    assert not HelperProfile.objects.exists()


def test_an_invalid_oib_keeps_the_previous_one(client: Client) -> None:
    luka = verified_user()
    save(client, luka, complete_input())

    body = save(client, luka, {"oib": "00000000699"})

    assert error_codes(body) == ["INVALID_OIB"]
    assert HelperProfile.objects.get().oib == OIB


def test_an_underage_helper_is_refused(client: Client) -> None:
    today = timezone.localdate()
    seventeen = today.replace(year=today.year - 17).isoformat()

    body = save(client, verified_user(), {"birthDate": seventeen})

    assert error_codes(body) == ["UNDERAGE"]


def test_one_oib_belongs_to_one_helper(client: Client) -> None:
    save(client, verified_user("luka"), complete_input())

    body = save(client, verified_user("ivo"), {"oib": OIB})

    assert error_codes(body) == ["OIB_IN_USE"]
    assert HelperProfile.objects.count() == 1


def test_any_eu_member_state_is_a_valid_residence_country(client: Client) -> None:
    saved = save(client, verified_user(), {**complete_input(), "residenceCountry": "de"})
    assert saved["data"]["updateHelperProfile"]["residenceCountry"] == "DE"
    assert saved["data"]["updateHelperProfile"]["canSendOffers"] is True


def test_a_croatian_vat_id_is_hr_and_the_helpers_oib_and_can_be_removed(client: Client) -> None:
    luka = verified_user()
    added = save(client, luka, {"oib": OIB, "vatId": "hr 00000000698"})
    assert added["data"]["updateHelperProfile"]["vatIdMasked"] == "HR•••••••0698"
    assert HelperProfile.objects.get().vat_id == "HR00000000698"

    removed = save(client, luka, {"vatId": ""})
    assert removed["data"]["updateHelperProfile"]["vatIdMasked"] is None


def test_a_croatian_vat_id_with_someone_elses_oib_is_refused(client: Client) -> None:
    luka = verified_user()

    body = save(client, luka, {"oib": OIB, "vatId": f"HR{OTHER_OIB}"})
    assert error_codes(body) == ["INVALID_VAT_ID"]
    # Without an OIB on the profile, no Croatian VAT ID can match.
    assert error_codes(save(client, luka, {"vatId": f"HR{OIB}"})) == ["INVALID_VAT_ID"]
    # Other countries' numbers can't be checked against the OIB.
    other = save(client, luka, {"vatId": "ATU12345678"})
    assert other["data"]["updateHelperProfile"]["vatIdMasked"] == "AT•••••5678"


def test_a_companys_vat_id_may_carry_the_company_oib(client: Client) -> None:
    business = {"kind": "COMPANY", "name": "Beavo d.o.o.", "oib": OTHER_OIB}
    changes = {"status": "BUSINESS", "business": business, "vatId": f"HR{OTHER_OIB}"}

    saved = save(client, verified_user(), {**complete_input(), **changes})

    assert saved["data"]["updateHelperProfile"]["missing"] == [
        "BUSINESS_VERIFICATION",
        "BUSINESS_ADDRESS",
    ]
    assert HelperProfile.objects.get().vat_id == f"HR{OTHER_OIB}"


def test_changing_the_oib_clears_a_croatian_vat_id_that_no_longer_matches(
    client: Client,
) -> None:
    luka = verified_user()
    save(client, luka, {"oib": OIB, "vatId": f"HR{OIB}", "residenceCountry": "HR"})

    saved = save(client, luka, {"oib": OTHER_OIB})

    assert saved["data"]["updateHelperProfile"]["vatIdMasked"] is None
    assert HelperProfile.objects.get().vat_id == ""


def test_a_non_croatian_address_is_refused_and_the_helper_can_join_the_waitlist(
    client: Client,
) -> None:
    luka = verified_user()
    vienna = {"street": "Stephansplatz 1", "postalCode": "1010", "city": "Wien", "country": "AT"}

    body = save(client, luka, {**complete_input(), "address": vienna})

    assert error_codes(body) == ["HELPER_ADDRESS_NOT_IN_CROATIA"]
    assert not HelperProfile.objects.exists()
    # The client then offers the waitlist for the helper's city.
    joined = gql(
        client,
        JOIN_WAITLIST,
        {
            "input": {
                "email": luka.email,
                "city": vienna["city"],
                "countryCode": vienna["country"],
                "category": "GARDEN",
                "locale": "DE",
                "consentLaunchEmail": True,
            }
        },
    )
    assert joined["data"]["joinWaitlist"]["countryCode"] == "AT"
    assert WaitlistEntry.objects.get().email == luka.email


def test_oib_business_oib_iban_and_vat_id_are_encrypted_at_rest(client: Client) -> None:
    business = {
        "kind": "COMPANY",
        "name": "Beavo d.o.o.",
        "oib": OTHER_OIB,
        "issuesFiscalizedInvoices": True,
    }
    changes = {"status": "BUSINESS", "business": business, "vatId": f"HR{OIB}"}
    save(client, verified_user(), {**complete_input(), **changes})

    with connection.cursor() as cursor:
        cursor.execute("SELECT oib, business_oib, iban, vat_id, oib_digest FROM helper_profiles")
        row = cursor.fetchone()
    stored = " ".join(row)
    for secret in (OIB, OTHER_OIB, IBAN, "0698", "0160"):
        assert secret not in stored
    profile = HelperProfile.objects.get()
    assert (profile.oib, profile.business_oib, profile.iban) == (OIB, OTHER_OIB, IBAN)


def test_a_business_needs_its_details_and_is_a_trader(client: Client) -> None:
    luka = verified_user()

    without = save(client, luka, {**complete_input(), "status": "BUSINESS"})
    assert without["data"]["updateHelperProfile"]["isTrader"] is True
    assert without["data"]["updateHelperProfile"]["missing"] == [
        "BUSINESS_DETAILS",
        "BUSINESS_ADDRESS",
    ]

    business = {
        "kind": "COMPANY",
        "name": "Beavo d.o.o.",
        "oib": OTHER_OIB,
        "issuesFiscalizedInvoices": True,
    }
    saved = save(client, luka, {"business": business})["data"]["updateHelperProfile"]
    assert saved["missing"] == ["BUSINESS_VERIFICATION", "BUSINESS_ADDRESS"]
    assert (saved["canSendOffers"], saved["businessVerified"]) == (False, False)
    assert (saved["businessKind"], saved["businessName"], saved["businessOib"]) == (
        "COMPANY",
        "Beavo d.o.o.",
        OTHER_OIB,
    )
    assert saved["issuesFiscalizedInvoices"] is True

    verify_company(luka)
    mine = gql(client, MINE, token=sign_in(luka).access_token)["data"]["myHelperProfile"]
    assert (mine["missing"], mine["businessVerified"]) == (["BUSINESS_ADDRESS"], True)
    give_business_address(luka)
    mine = gql(client, MINE, token=sign_in(luka).access_token)["data"]["myHelperProfile"]
    assert (mine["canSendOffers"], mine["businessVerified"]) == (True, True)


BUSINESS_ADDRESS = {
    "street": "Obala Stjepana Radića 12",
    "postalCode": "20000",
    "city": "Dubrovnik",
    "country": "hr",
}


def test_onboarding_takes_the_registered_business_address(client: Client) -> None:
    luka = verified_user()
    obrt = {"kind": "OBRT", "name": "Obrt Luka", "issuesFiscalizedInvoices": True}
    save(client, luka, {**complete_input(), "status": "BUSINESS", "business": obrt})

    saved = save(client, luka, {"businessAddress": BUSINESS_ADDRESS})["data"]

    mine = saved["updateHelperProfile"]
    assert (mine["missing"], mine["canSendOffers"]) == ([], True)
    assert (
        mine["businessAddressStreet"],
        mine["businessAddressPostalCode"],
        mine["businessAddressCity"],
        mine["businessAddressCountry"],
    ) == ("Obala Stjepana Radića 12", "20000", "Dubrovnik", "HR")
    profile = HelperProfile.objects.get()
    assert profile.business_address == "Obala Stjepana Radića 12, 20000 Dubrovnik, HR"
    # The home address is separate and unchanged.
    assert profile.address_street == "Ulica Ante Starčevića 12"


def test_a_business_registered_elsewhere_in_the_eu_is_accepted(client: Client) -> None:
    luka = verified_user()
    obrt = {"kind": "OBRT", "name": "Obrt Luka", "issuesFiscalizedInvoices": True}
    address = {"street": "Hauptstraße 1", "postalCode": "1010", "city": "Wien", "country": "AT"}

    body = save(
        client,
        luka,
        {**complete_input(), "status": "BUSINESS", "business": obrt, "businessAddress": address},
    )

    assert body["data"]["updateHelperProfile"]["businessAddressCountry"] == "AT"


@pytest.mark.parametrize(
    "address",
    [
        {**BUSINESS_ADDRESS, "country": "US"},
        {**BUSINESS_ADDRESS, "postalCode": "2000"},
        {**BUSINESS_ADDRESS, "street": " "},
        {**BUSINESS_ADDRESS, "city": "x" * 101},
        # Posters see it before booking: no phone, email or link smuggled in (rule e).
        {**BUSINESS_ADDRESS, "street": "Obala 12, nazovite 091 234 5678"},
        {**BUSINESS_ADDRESS, "city": "Dubrovnik luka@example.com"},
        {**BUSINESS_ADDRESS, "street": "www.obrt-luka.hr"},
    ],
)
def test_an_invalid_business_address_is_refused(client: Client, address: dict[str, str]) -> None:
    luka = verified_user()
    obrt = {"kind": "OBRT", "name": "Obrt Luka"}
    save(client, luka, {**complete_input(), "status": "BUSINESS", "business": obrt})

    body = save(client, luka, {"businessAddress": address})

    assert error_codes(body) == ["INVALID_HELPER_DETAILS"]
    assert HelperProfile.objects.get().business_address == ""


def test_a_private_helper_cant_set_a_business_address(client: Client) -> None:
    body = save(client, verified_user(), {**complete_input(), "businessAddress": BUSINESS_ADDRESS})

    assert error_codes(body) == ["INVALID_HELPER_DETAILS"]
    assert not HelperProfile.objects.exists()


def test_leaving_the_business_status_removes_the_business_address(client: Client) -> None:
    luka = verified_user()
    obrt = {"kind": "OBRT", "name": "Obrt Luka"}
    save(
        client,
        luka,
        {
            **complete_input(),
            "status": "BUSINESS",
            "business": obrt,
            "businessAddress": BUSINESS_ADDRESS,
        },
    )

    saved = save(client, luka, {"status": "PENSIONER"})["data"]["updateHelperProfile"]

    assert saved["businessAddressStreet"] is None
    assert HelperProfile.objects.get().business_address == ""


@pytest.mark.parametrize(
    "change",
    [
        {"name": "Beavo j.d.o.o."},
        {"oib": "00000000001"},
        {"kind": "OBRT", "oib": None},
    ],
)
def test_changing_a_companys_name_or_oib_clears_its_verification(
    client: Client, change: dict[str, Any]
) -> None:
    luka = verified_user()
    company = {"kind": "COMPANY", "name": "Beavo d.o.o.", "oib": OTHER_OIB}
    save(client, luka, {**complete_input(), "status": "BUSINESS", "business": company})
    verify_company(luka)

    save(client, luka, {"business": {**company, **change}})

    assert HelperProfile.objects.get().business_verified_at is None


def test_saving_the_same_company_details_keeps_the_verification(client: Client) -> None:
    luka = verified_user()
    company = {"kind": "COMPANY", "name": "Beavo d.o.o.", "oib": OTHER_OIB}
    save(client, luka, {**complete_input(), "status": "BUSINESS", "business": company})
    verify_company(luka)
    give_business_address(luka)

    saved = save(client, luka, {"business": {**company, "issuesFiscalizedInvoices": True}})

    assert saved["data"]["updateHelperProfile"]["canSendOffers"] is True


def test_leaving_the_business_status_clears_the_verification(client: Client) -> None:
    luka = verified_user()
    company = {"kind": "COMPANY", "name": "Beavo d.o.o.", "oib": OTHER_OIB}
    save(client, luka, {**complete_input(), "status": "BUSINESS", "business": company})
    verify_company(luka)

    save(client, luka, {"status": "PENSIONER"})
    save(client, luka, {"status": "BUSINESS", "business": company})

    profile = HelperProfile.objects.get()
    assert (profile.business_verified_at, profile.business_verified_by) == (None, "")


def test_an_obrt_takes_its_owners_oib_when_the_business_comes_first(client: Client) -> None:
    luka = verified_user()
    obrt = {"kind": "OBRT", "name": "Obrt Luka", "issuesFiscalizedInvoices": True}
    without_oib = {k: v for k, v in complete_input().items() if k != "oib"}

    first = save(client, luka, {**without_oib, "status": "BUSINESS", "business": obrt})
    assert first["data"]["updateHelperProfile"]["missing"] == [
        "BUSINESS_DETAILS",
        "BUSINESS_ADDRESS",
        "OIB",
    ]
    give_business_address(luka)

    saved = save(client, luka, {"oib": OIB})["data"]["updateHelperProfile"]
    assert saved["canSendOffers"] is True
    assert saved["businessOib"] == OIB


def test_an_obrt_takes_its_owners_oib_when_the_oib_comes_first(client: Client) -> None:
    luka = verified_user()
    save(client, luka, complete_input())
    obrt = {"kind": "OBRT", "name": "Obrt Luka", "issuesFiscalizedInvoices": True}

    saved = save(client, luka, {"status": "BUSINESS", "business": obrt})["data"]
    assert saved["updateHelperProfile"]["missing"] == ["BUSINESS_ADDRESS"]
    give_business_address(luka)
    saved = save(client, luka, {"business": obrt})["data"]
    assert saved["updateHelperProfile"]["canSendOffers"] is True
    assert saved["updateHelperProfile"]["businessOib"] == OIB
    # Giving the owner's own OIB is accepted too.
    same = save(client, luka, {"business": {**obrt, "oib": OIB}})
    assert same["data"]["updateHelperProfile"]["businessOib"] == OIB

    # A corrected personal OIB carries over to the obrt.
    changed = save(client, luka, {"oib": OTHER_OIB})["data"]["updateHelperProfile"]
    assert (changed["businessOib"], changed["canSendOffers"]) == (OTHER_OIB, True)


def test_an_obrt_with_another_oib_is_refused(client: Client) -> None:
    luka = verified_user()
    save(client, luka, complete_input())
    obrt = {"kind": "OBRT", "name": "Obrt Luka", "oib": OTHER_OIB, "issuesFiscalizedInvoices": True}

    body = save(client, luka, {"status": "BUSINESS", "business": obrt})

    assert error_codes(body) == ["INVALID_BUSINESS_OIB"]
    assert HelperProfile.objects.get().declared_status == "PRIVATE_INDIVIDUAL"


def test_a_company_oib_cant_be_the_owners_personal_oib(client: Client) -> None:
    company = {"kind": "COMPANY", "name": "Beavo d.o.o.", "oib": OIB}

    body = save(
        client, verified_user(), {**complete_input(), "status": "BUSINESS", "business": company}
    )

    assert error_codes(body) == ["INVALID_BUSINESS_OIB"]
    assert not HelperProfile.objects.exists()


def test_a_company_oib_cant_be_another_helpers_personal_oib(client: Client) -> None:
    save(client, verified_user("ivo"), {"oib": OTHER_OIB})
    company = {"kind": "COMPANY", "name": "Beavo d.o.o.", "oib": OTHER_OIB}

    body = save(client, verified_user("luka"), {"status": "BUSINESS", "business": company})

    assert error_codes(body) == ["INVALID_BUSINESS_OIB"]


def test_a_personal_oib_cant_be_another_helpers_company_oib(client: Client) -> None:
    company = {"kind": "COMPANY", "name": "Beavo d.o.o.", "oib": OTHER_OIB}
    save(client, verified_user("luka"), {"status": "BUSINESS", "business": company})

    body = save(client, verified_user("ivo"), {"oib": OTHER_OIB})

    assert error_codes(body) == ["OIB_IN_USE"]


def test_a_company_needs_its_oib(client: Client) -> None:
    company = {"kind": "COMPANY", "name": "Beavo d.o.o."}

    body = save(client, verified_user(), {"status": "BUSINESS", "business": company})

    assert error_codes(body) == ["INVALID_HELPER_DETAILS"]


def test_business_details_need_the_business_status(client: Client) -> None:
    business = {"kind": "OBRT", "name": "Obrt Luka", "issuesFiscalizedInvoices": False}

    body = save(client, verified_user(), {"status": "PENSIONER", "business": business})

    assert error_codes(body) == ["INVALID_HELPER_DETAILS"]


def test_an_invalid_company_oib_is_refused(client: Client) -> None:
    company = {"kind": "COMPANY", "name": "Beavo d.o.o.", "oib": "12345678901"}

    body = save(client, verified_user(), {"status": "BUSINESS", "business": company})

    assert error_codes(body) == ["INVALID_OIB"]


def test_leaving_the_business_status_removes_the_business_details(client: Client) -> None:
    luka = verified_user()
    company = {"kind": "COMPANY", "name": "Beavo d.o.o.", "oib": OTHER_OIB}
    save(client, luka, {"status": "BUSINESS", "business": company})

    saved = save(client, luka, {"status": "PRIVATE_INDIVIDUAL"})["data"]["updateHelperProfile"]

    assert (saved["isTrader"], saved["businessName"], saved["businessOib"]) == (False, None, None)
    profile = HelperProfile.objects.get()
    assert (profile.business_oib, profile.business_oib_digest) == ("", "")
    # The company OIB is free again for a person.
    assert error_codes(save(client, verified_user("ivo"), {"oib": OTHER_OIB})) == []


def test_the_public_profile_of_a_trader_shows_business_name_oib_and_address(
    client: Client,
) -> None:
    luka = verified_user()
    business = {
        "kind": "COMPANY",
        "name": "Beavo d.o.o.",
        "oib": OTHER_OIB,
        "issuesFiscalizedInvoices": True,
    }
    save(client, luka, {**complete_input(), "status": "BUSINESS", "business": business})
    give_business_address(luka)
    # Unverified, the company isn't public at all.
    assert gql(client, PUBLIC, {"userId": str(luka.pk)})["data"]["helperProfile"] is None
    verify_company(luka)

    body = gql(client, PUBLIC, {"userId": str(luka.pk)})

    public = body["data"]["helperProfile"]
    assert public["isTrader"] is True
    assert (public["businessName"], public["businessOib"]) == ("Beavo d.o.o.", OTHER_OIB)
    # Only the town: many obrts are registered at the owner's home, so the street address
    # is for the poster of a task the helper offered on (user decision 2026-11-07).
    assert public["businessTown"] == "Dubrovnik"
    assert public["categories"] == ["GARDEN", "REPAIRS"]
    # Never the street address, the owner's OIB, IBAN, legal name, home address or birth date.
    for private in (OIB, "0698", IBAN, "0160", "Horvat", "Starčevića", "2002", "Obala", "20000"):
        assert private not in str(body)
    signed_in = gql(
        client, PUBLIC, {"userId": str(luka.pk)}, token=sign_in(make_user("ana")).access_token
    )
    assert "Obala" not in str(signed_in)
    assert signed_in["data"]["helperProfile"]["businessTown"] == "Dubrovnik"


def test_the_public_profile_of_a_private_individual_has_no_business_data(client: Client) -> None:
    luka = verified_user()
    save(client, luka, complete_input())

    other = sign_in(make_user("ana")).access_token
    body = gql(client, PUBLIC, {"userId": str(luka.pk)}, token=other)

    public = body["data"]["helperProfile"]
    assert (
        public["isTrader"],
        public["businessName"],
        public["businessOib"],
        public["businessTown"],
    ) == (False, None, None, None)
    for private in (OIB, IBAN, "Horvat", "Starčevića", "2002"):
        assert private not in str(body)


def test_an_incomplete_or_missing_profile_has_no_public_profile(client: Client) -> None:
    luka = verified_user()
    save(client, luka, {"status": "STUDENT"})

    assert gql(client, PUBLIC, {"userId": str(luka.pk)})["data"]["helperProfile"] is None
    assert gql(client, PUBLIC, {"userId": "999999"})["data"]["helperProfile"] is None
    assert gql(client, PUBLIC, {"userId": "x"})["data"]["helperProfile"] is None


def test_the_owner_query_only_ever_returns_the_callers_profile(client: Client) -> None:
    save(client, verified_user("luka"), complete_input())

    body = gql(client, MINE, token=sign_in(make_user("ana")).access_token)

    assert body == {"data": {"myHelperProfile": None}}


def test_service_areas_lists_the_active_croatian_areas(client: Client) -> None:
    body = gql(client, "{ serviceAreas { id name slug } }")
    assert [a["slug"] for a in body["data"]["serviceAreas"]] == ["dubrovnik"]


def test_update_helper_profile_is_rate_limited(client: Client, settings: Any) -> None:
    settings.GRAPHQL_MUTATION_RATE_LIMIT_OVERRIDES = {"updateHelperProfile": 2}
    luka = verified_user()

    codes = [error_codes(save(client, luka, {"status": "STUDENT"})) for _ in range(3)]

    assert codes == [[], [], ["RATE_LIMITED"]]


def test_identity_and_tax_data_stay_out_of_spans(client: Client, spans: Any) -> None:
    save(client, verified_user(), {**complete_input(), "vatId": "HR00000000698"})

    exported = str([(s.name, dict(s.attributes or {})) for s in spans.get_finished_spans()])
    assert spans.get_finished_spans()
    for secret in (OIB, "0698", IBAN, "Horvat", "Starčevića", "2002-03-14", "20000"):
        assert secret not in exported


PAYOUT = "payoutAccount { id status payoutsEnabled requirementsDue currentDeadline rejection }"
START_ONBOARDING = f"""
mutation {{
  startPayoutOnboarding {{ id url expiresAt helperProfile {{ id canSendOffers {PAYOUT} }} }}
}}
"""


def test_the_owner_sees_the_payout_account_without_stripe_ids(client: Client) -> None:
    luka = verified_user()
    save(client, luka, complete_input())

    query = f"{{ myHelperProfile {{ {PAYOUT} }} }}"
    mine = gql(client, query, token=sign_in(luka).access_token)["data"]["myHelperProfile"]

    account = ConnectedAccount.objects.get()
    assert mine["payoutAccount"] == {
        "id": str(account.pk),
        "status": "ACTIVE",
        "payoutsEnabled": True,
        "requirementsDue": [],
        "currentDeadline": None,
        "rejection": None,
    }
    assert account.stripe_account_id not in str(mine)


@pytest.mark.parametrize(
    ("disabled_reason", "rejection"),
    [
        ("rejected.incomplete_verification", "INCOMPLETE_VERIFICATION"),
        ("rejected.terms_of_service", "TERMS_OF_SERVICE"),
        ("rejected.fraud", "OTHER"),
        ("rejected.listed", "OTHER"),
        ("rejected.other", "OTHER"),
    ],
)
def test_a_rejection_shows_only_its_category(
    client: Client, disabled_reason: str, rejection: str
) -> None:
    luka = verified_user()
    save(client, luka, complete_input())
    ConnectedAccount.objects.update(
        status=ConnectedAccount.Status.REJECTED, disabled_reason=disabled_reason
    )

    query = f"{{ myHelperProfile {{ missing {PAYOUT} }} }}"
    mine = gql(client, query, token=sign_in(luka).access_token)["data"]["myHelperProfile"]

    assert mine["payoutAccount"]["status"] == "REJECTED"
    assert mine["payoutAccount"]["rejection"] == rejection
    assert mine["missing"] == ["PAYOUT_ACCOUNT"]


def test_start_payout_onboarding_returns_the_link_and_the_profile(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    luka = verified_user()
    save(client, luka, complete_input())
    ConnectedAccount.objects.update(status=ConnectedAccount.Status.NEEDS_INFO)
    link = SimpleNamespace(url="https://connect.stripe.com/setup/e/x", expires_at=1793516700)
    gateway = SimpleNamespace(create_onboarding_link=lambda **kwargs: link)
    monkeypatch.setattr(connect, "get_gateway", lambda: gateway)

    body = gql(client, START_ONBOARDING, token=sign_in(luka).access_token)

    started = body["data"]["startPayoutOnboarding"]
    assert started["id"] == str(ConnectedAccount.objects.get().pk)
    assert started["url"] == "https://connect.stripe.com/setup/e/x"
    assert started["expiresAt"] == "2026-11-01T07:05:00+00:00"
    assert started["helperProfile"]["canSendOffers"] is False
    assert started["helperProfile"]["payoutAccount"]["status"] == "NEEDS_INFO"


def test_start_payout_onboarding_needs_a_signed_in_helper(client: Client) -> None:
    assert error_codes(gql(client, START_ONBOARDING)) == ["UNAUTHENTICATED"]
    body = gql(client, START_ONBOARDING, token=sign_in(verified_user()).access_token)
    assert error_codes(body) == ["HELPER_PROFILE_INCOMPLETE"]


def test_start_payout_onboarding_is_rate_limited(
    client: Client, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.GRAPHQL_MUTATION_RATE_LIMIT_OVERRIDES = {"startPayoutOnboarding": 2}
    luka = verified_user()
    save(client, luka, complete_input())
    link = SimpleNamespace(url="https://connect.stripe.com/setup/e/x", expires_at=1793516700)
    monkeypatch.setattr(
        connect,
        "get_gateway",
        lambda: SimpleNamespace(create_onboarding_link=lambda **kwargs: link),
    )
    token = sign_in(luka).access_token

    codes = [error_codes(gql(client, START_ONBOARDING, token=token)) for _ in range(3)]

    assert codes == [[], [], ["RATE_LIMITED"]]
