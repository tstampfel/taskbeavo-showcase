"""On-site contacts over HTTP at /graphql: owner-only CRUD (plan: "GDPR flows")."""

from typing import Any

import pytest
from django.core.cache import caches
from django.test import Client

from accounts.tests.factories import make_user, sign_in
from accounts.tests.test_schema import error_codes, gql
from care_contacts.models import CareContact
from care_contacts.tests.factories import make_contact

pytestmark = pytest.mark.django_db

FIELDS = "id name phone smsCapable area language noSmartphone createdAt updatedAt"
LIST = f"{{ myCareContacts {{ {FIELDS} }} }}"
CREATE = f"""
mutation($input: CreateCareContactInput!) {{ createCareContact(input: $input) {{ {FIELDS} }} }}
"""
UPDATE = f"""
mutation($id: ID!, $input: UpdateCareContactInput!) {{
  updateCareContact(id: $id, input: $input) {{ {FIELDS} }}
}}
"""
DELETE = f"mutation($id: ID!) {{ deleteCareContact(id: $id) {{ {FIELDS} }} }}"

NEW_CONTACT: dict[str, Any] = {
    "name": "Mama Kata",
    "phone": "091 234 5678",
    "area": "Gruž, Dubrovnik",
    "language": "HR",
    "noSmartphone": True,
    "hasTheirAgreement": True,
}


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> None:
    caches["graphql_rate_limit"].clear()


def test_signed_out_callers_are_refused(client: Client) -> None:
    contact = make_contact(make_user())
    assert error_codes(gql(client, LIST)) == ["UNAUTHENTICATED"]
    assert error_codes(gql(client, CREATE, {"input": NEW_CONTACT})) == ["UNAUTHENTICATED"]
    assert error_codes(gql(client, UPDATE, {"id": contact.pk, "input": {"name": "X"}})) == [
        "UNAUTHENTICATED"
    ]
    assert error_codes(gql(client, DELETE, {"id": contact.pk})) == ["UNAUTHENTICATED"]
    assert CareContact.objects.get().name == "Mama Kata"


def test_creating_a_contact_returns_the_full_object(client: Client) -> None:
    ana = make_user("ana")
    token = sign_in(ana).access_token

    body = gql(client, CREATE, {"input": NEW_CONTACT}, token=token)

    created = body["data"]["createCareContact"]
    saved = CareContact.objects.get()
    assert created["id"] == str(saved.pk)
    keys = ("name", "phone", "smsCapable", "area", "language", "noSmartphone")
    assert {k: created[k] for k in keys} == {
        "name": "Mama Kata",
        "phone": "+385912345678",
        "smsCapable": True,
        "area": "Gruž, Dubrovnik",
        "language": "HR",
        "noSmartphone": True,
    }
    assert created["createdAt"] and created["updatedAt"]
    assert saved.owner == ana
    assert saved.agreement_confirmed_at is not None


def test_the_phone_is_encrypted_at_rest(client: Client) -> None:
    token = sign_in(make_user()).access_token
    gql(client, CREATE, {"input": NEW_CONTACT}, token=token)

    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SELECT phone_number FROM care_contacts")
        (stored,) = cursor.fetchone()
    assert "385912345678" not in stored
    assert CareContact.objects.get().phone_number == "+385912345678"


def test_the_contacts_agreement_is_required(client: Client) -> None:
    token = sign_in(make_user()).access_token

    body = gql(client, CREATE, {"input": {**NEW_CONTACT, "hasTheirAgreement": False}}, token=token)

    assert error_codes(body) == ["CARE_CONTACT_AGREEMENT_REQUIRED"]
    assert not CareContact.objects.exists()


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"phone": "not a number"}, "INVALID_CARE_CONTACT_PHONE"),
        ({"phone": "123"}, "INVALID_CARE_CONTACT_PHONE"),
        ({"name": "   "}, "INVALID_CARE_CONTACT"),
        ({"name": "x" * 101}, "INVALID_CARE_CONTACT"),
        ({"area": ""}, "INVALID_CARE_CONTACT"),
        ({"area": "x" * 101}, "INVALID_CARE_CONTACT"),
    ],
)
def test_invalid_details_are_refused(client: Client, change: dict[str, str], code: str) -> None:
    token = sign_in(make_user()).access_token

    body = gql(client, CREATE, {"input": {**NEW_CONTACT, **change}}, token=token)

    assert error_codes(body) == [code]
    assert not CareContact.objects.exists()


def test_international_numbers_are_accepted(client: Client) -> None:
    token = sign_in(make_user()).access_token
    body = gql(client, CREATE, {"input": {**NEW_CONTACT, "phone": "+49 151 23456789"}}, token=token)
    assert body["data"]["createCareContact"]["phone"] == "+4915123456789"


def test_a_landline_is_accepted_and_marked_as_not_sms_capable(client: Client) -> None:
    token = sign_in(make_user()).access_token

    body = gql(client, CREATE, {"input": {**NEW_CONTACT, "phone": "020 123 456"}}, token=token)

    created = body["data"]["createCareContact"]
    assert (created["phone"], created["smsCapable"]) == ("+38520123456", False)
    assert CareContact.objects.get().sms_capable is False


def test_changing_to_a_landline_updates_sms_capable(client: Client) -> None:
    ana = make_user("ana")
    contact = make_contact(ana)

    body = gql(
        client,
        UPDATE,
        {"id": contact.pk, "input": {"phone": "+385 20 123 456"}},
        token=sign_in(ana).access_token,
    )

    assert body["data"]["updateCareContact"]["smsCapable"] is False
    contact.refresh_from_db()
    assert (contact.phone_number, contact.sms_capable) == ("+38520123456", False)


def test_names_and_areas_are_trimmed(client: Client) -> None:
    token = sign_in(make_user()).access_token
    variables = {"input": {**NEW_CONTACT, "name": "  Mama Kata ", "area": " Gruž "}}

    created = gql(client, CREATE, variables, token=token)["data"]["createCareContact"]

    assert (created["name"], created["area"]) == ("Mama Kata", "Gruž")


def test_the_list_holds_only_the_viewers_own_contacts(client: Client) -> None:
    ana, luka = make_user("ana"), make_user("luka")
    mine = make_contact(ana)
    make_contact(ana, name="Ujak Ivo", area="Konavle")
    make_contact(luka, name="Baka Mare")

    found = gql(client, LIST, token=sign_in(ana).access_token)["data"]["myCareContacts"]

    assert [c["name"] for c in found] == ["Mama Kata", "Ujak Ivo"]
    assert found[0]["id"] == str(mine.pk)
    assert found[0]["phone"] == "+385912345678"


def test_updating_changes_only_the_given_fields(client: Client) -> None:
    ana = make_user("ana")
    contact = make_contact(ana)
    token = sign_in(ana).access_token

    body = gql(
        client,
        UPDATE,
        {"id": contact.pk, "input": {"phone": "+385 98 111 2222", "language": "DE"}},
        token=token,
    )

    updated = body["data"]["updateCareContact"]
    assert updated["id"] == str(contact.pk)
    assert updated["phone"] == "+385981112222"
    assert updated["language"] == "DE"
    assert updated["name"] == "Mama Kata"
    assert updated["noSmartphone"] is False
    contact.refresh_from_db()
    assert contact.phone_number == "+385981112222"


def test_an_update_with_an_invalid_phone_changes_nothing(client: Client) -> None:
    ana = make_user("ana")
    contact = make_contact(ana)

    body = gql(
        client,
        UPDATE,
        {"id": contact.pk, "input": {"name": "Mama", "phone": "123"}},
        token=sign_in(ana).access_token,
    )

    assert error_codes(body) == ["INVALID_CARE_CONTACT_PHONE"]
    contact.refresh_from_db()
    assert (contact.name, contact.phone_number) == ("Mama Kata", "+385912345678")


def test_another_users_contact_cant_be_changed_or_deleted(client: Client) -> None:
    contact = make_contact(make_user("ana"))
    token = sign_in(make_user("luka")).access_token

    updated = gql(client, UPDATE, {"id": contact.pk, "input": {"name": "Hacked"}}, token=token)
    deleted = gql(client, DELETE, {"id": contact.pk}, token=token)

    assert error_codes(updated) == ["CARE_CONTACT_NOT_FOUND"]
    assert error_codes(deleted) == ["CARE_CONTACT_NOT_FOUND"]
    assert "+385" not in str(updated) + str(deleted)
    contact.refresh_from_db()
    assert contact.name == "Mama Kata"


def test_unknown_ids_are_not_found(client: Client) -> None:
    token = sign_in(make_user()).access_token
    for variables in ({"id": "999999"}, {"id": "abc"}):
        assert error_codes(gql(client, DELETE, variables, token=token)) == [
            "CARE_CONTACT_NOT_FOUND"
        ]


def test_deleting_returns_the_removed_contact(client: Client) -> None:
    ana = make_user("ana")
    contact = make_contact(ana)

    body = gql(client, DELETE, {"id": contact.pk}, token=sign_in(ana).access_token)

    assert body["data"]["deleteCareContact"]["id"] == str(contact.pk)
    assert body["data"]["deleteCareContact"]["name"] == "Mama Kata"
    assert not CareContact.objects.exists()


def test_care_contact_mutations_are_rate_limited(client: Client, settings: Any) -> None:
    settings.GRAPHQL_MUTATION_RATE_LIMIT_OVERRIDES = {"createCareContact": 2}
    token = sign_in(make_user()).access_token

    codes = [
        error_codes(gql(client, CREATE, {"input": NEW_CONTACT}, token=token)) for _ in range(3)
    ]

    assert codes == [[], [], ["RATE_LIMITED"]]
    assert CareContact.objects.count() == 2


def test_the_contacts_name_and_phone_stay_out_of_spans(client: Client, spans: Any) -> None:
    token = sign_in(make_user()).access_token

    gql(client, CREATE, {"input": NEW_CONTACT}, token=token)

    exported = str([(s.name, dict(s.attributes or {})) for s in spans.get_finished_spans()])
    assert spans.get_finished_spans()
    for secret in ("Mama Kata", "385912345678", "234 5678", "Gruž"):
        assert secret not in exported
