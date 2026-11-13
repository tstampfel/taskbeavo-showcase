"""joinWaitlist and leaveWaitlist over HTTP at /graphql."""

from typing import Any

import pytest
from django.contrib import admin
from django.contrib.gis.admin import GISModelAdmin
from django.core.cache import caches
from django.test import Client, override_settings
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from accounts.tests.factories import make_user, sign_in
from accounts.tests.test_schema import error_codes, gql
from areas.models import WAITLIST_CONSENT_VERSION, ServiceArea, WaitlistEntry
from areas.services import issue_leave_token

pytestmark = pytest.mark.django_db

JOIN = """
mutation($input: JoinWaitlistInput!) {
  joinWaitlist(input: $input) { id city countryCode category }
}
"""
LEAVE = "mutation($token: String!) { leaveWaitlist(token: $token) }"


def details(**overrides: Any) -> dict[str, Any]:
    return {
        "email": "Ana.Horvat@Example.com",
        "city": "  Osijek ",
        "countryCode": "hr",
        "category": "GARDEN",
        "locale": "DE",
        "consentLaunchEmail": True,
    } | overrides


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> None:
    """Every test starts with empty mutation rate-limit counters."""
    caches["graphql_rate_limit"].clear()


def test_joining_stores_city_country_category_and_the_consent(client: Client) -> None:
    body = gql(client, JOIN, {"input": details()})

    joined = body["data"]["joinWaitlist"]
    entry = WaitlistEntry.objects.get()
    assert joined == {
        "id": str(entry.pk),
        "city": "Osijek",
        "countryCode": "HR",
        "category": "GARDEN",
    }
    # Stored as accounts store addresses, so deletion and export find it by exact match.
    assert entry.email == "ana.horvat@example.com"
    assert (entry.city, entry.city_key, entry.country_code, entry.category) == (
        "Osijek",
        "osijek",
        "HR",
        "GARDEN",
    )
    # The consent can be proven: what was agreed, when, and in which language.
    assert entry.consent_launch_email is True
    assert entry.consent_version == WAITLIST_CONSENT_VERSION
    assert entry.consent_given_at is not None
    assert entry.consent_locale == "de"
    assert entry.launch_email_sent_at is None


def test_a_signed_in_user_can_join_too(client: Client) -> None:
    token = sign_in(make_user()).access_token
    body = gql(client, JOIN, {"input": details(countryCode="AT", city="Wien")}, token=token)
    assert body["data"]["joinWaitlist"]["countryCode"] == "AT"


def test_joining_without_consent_is_refused_and_stores_nothing(client: Client) -> None:
    body = gql(client, JOIN, {"input": details(consentLaunchEmail=False)})
    assert error_codes(body) == ["WAITLIST_CONSENT_REQUIRED"]
    assert not WaitlistEntry.objects.exists()


@pytest.mark.parametrize(
    "email",
    [
        "not-an-email",
        # validate_email accepts up to 320 characters; the column holds 254.
        "a" * 243 + "@example.com",
    ],
)
def test_an_invalid_email_is_refused(client: Client, email: str) -> None:
    body = gql(client, JOIN, {"input": details(email=email)})
    assert error_codes(body) == ["INVALID_EMAIL"]
    assert not WaitlistEntry.objects.exists()


@pytest.mark.parametrize(
    "overrides",
    [
        {"countryCode": "Croatia"},
        {"countryCode": "1A"},
        {"city": "   "},
        {"city": "x" * 101},
        {"city": "Osijek\x00"},
    ],
)
def test_invalid_city_or_country_is_refused(client: Client, overrides: dict[str, str]) -> None:
    body = gql(client, JOIN, {"input": details(**overrides)})
    assert error_codes(body) == ["INVALID_WAITLIST_DETAILS"]
    assert not WaitlistEntry.objects.exists()


def test_joining_again_for_the_same_city_updates_the_entry(client: Client) -> None:
    first = gql(client, JOIN, {"input": details()})["data"]["joinWaitlist"]
    again = gql(
        client,
        JOIN,
        {"input": details(email="ana.horvat@example.com", city="osijek", category="CLEANING")},
    )["data"]["joinWaitlist"]

    assert again["id"] == first["id"]
    entry = WaitlistEntry.objects.get()
    assert (entry.category, entry.city) == ("CLEANING", "osijek")


def test_rejoining_answers_like_a_first_join(client: Client) -> None:
    """Nobody can tell from the answer whether an address was already on the list."""
    gql(client, JOIN, {"input": details(city="split", category="CLEANING")})
    already_listed = gql(client, JOIN, {"input": details(city="Split")})["data"]["joinWaitlist"]
    first_time = gql(client, JOIN, {"input": details(city="Zadar")})["data"]["joinWaitlist"]

    assert set(already_listed) == set(first_time) == {"id", "city", "countryCode", "category"}
    assert already_listed | {"id": None} == {
        "id": None,
        "city": "Split",
        "countryCode": "HR",
        "category": "GARDEN",
    }


def test_another_city_is_another_entry(client: Client) -> None:
    gql(client, JOIN, {"input": details()})
    gql(client, JOIN, {"input": details(city="Split")})
    assert WaitlistEntry.objects.count() == 2


@override_settings(GRAPHQL_MUTATION_RATE_LIMIT_OVERRIDES={"joinWaitlist": 2})
def test_join_waitlist_is_rate_limited(client: Client) -> None:
    for city in ("Osijek", "Split"):
        assert "errors" not in gql(client, JOIN, {"input": details(city=city)})
    body = gql(client, JOIN, {"input": details(city="Zadar")})
    assert error_codes(body) == ["RATE_LIMITED"]
    assert WaitlistEntry.objects.count() == 2


def test_public_waitlist_mutations_have_their_own_limits_in_settings() -> None:
    from django.conf import settings

    limits = settings.GRAPHQL_MUTATION_RATE_LIMIT_OVERRIDES
    assert (limits["joinWaitlist"], limits["leaveWaitlist"]) == (5, 10)


def test_no_email_or_city_reaches_the_trace(client: Client, spans: InMemorySpanExporter) -> None:
    gql(client, JOIN, {"input": details()})

    recorded = repr([(s.name, dict(s.attributes or {})) for s in spans.get_finished_spans()])
    assert "example.com" not in recorded.lower()
    assert "osijek" not in recorded.lower()


def test_service_areas_are_edited_on_a_map_in_the_admin() -> None:
    assert isinstance(admin.site._registry[ServiceArea], GISModelAdmin)  # pyright: ignore[reportPrivateUsage]


def joined_entry(client: Client) -> WaitlistEntry:
    gql(client, JOIN, {"input": details()})
    return WaitlistEntry.objects.get()


def test_leaving_with_the_email_token_deletes_the_entry(client: Client) -> None:
    token = issue_leave_token(joined_entry(client))

    assert gql(client, LEAVE, {"token": token}) == {"data": {"leaveWaitlist": True}}
    assert not WaitlistEntry.objects.exists()


@pytest.mark.parametrize("token", ["", "unknown-token", "x" * 500])
def test_an_unknown_token_answers_the_same_and_deletes_nothing(client: Client, token: str) -> None:
    issue_leave_token(joined_entry(client))

    assert gql(client, LEAVE, {"token": token}) == {"data": {"leaveWaitlist": True}}
    assert WaitlistEntry.objects.count() == 1


def test_only_the_digest_of_the_token_is_stored(client: Client) -> None:
    entry = joined_entry(client)
    token = issue_leave_token(entry)
    entry.refresh_from_db()
    assert entry.leave_token_digest
    assert token not in entry.leave_token_digest


def test_a_reissued_token_replaces_the_old_one(client: Client) -> None:
    entry = joined_entry(client)
    old = issue_leave_token(entry)
    issue_leave_token(entry)

    gql(client, LEAVE, {"token": old})
    assert WaitlistEntry.objects.count() == 1


@override_settings(GRAPHQL_MUTATION_RATE_LIMIT_OVERRIDES={"leaveWaitlist": 2})
def test_leave_waitlist_is_rate_limited(client: Client) -> None:
    token = issue_leave_token(joined_entry(client))
    for guess in ("guess-1", "guess-2"):
        assert "errors" not in gql(client, LEAVE, {"token": guess})
    assert error_codes(gql(client, LEAVE, {"token": token})) == ["RATE_LIMITED"]
    assert WaitlistEntry.objects.count() == 1


def test_no_token_reaches_the_trace(client: Client, spans: InMemorySpanExporter) -> None:
    token = issue_leave_token(joined_entry(client))
    spans.clear()

    gql(client, LEAVE, {"token": token})

    recorded = repr([(s.name, dict(s.attributes or {})) for s in spans.get_finished_spans()])
    assert token not in recorded
