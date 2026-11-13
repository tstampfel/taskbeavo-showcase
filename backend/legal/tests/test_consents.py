"""Analytics and marketing consent (BEAV-83; plan, GDPR section "Correction, objection and
consent"): every change is a CONSENT row with the version of the text it was given under."""

import uuid

import pytest
from django.core.cache import caches
from django.test import Client

from accounts.models import Session
from accounts.tests.factories import make_user, sign_in
from accounts.tests.test_schema import error_codes, gql
from legal import consents
from legal.models import Consent, ImmutableRowError, LegalDocument
from legal.tests.factories import publish_version

pytestmark = pytest.mark.django_db

CONSENTS = "{ consents { id purpose granted version givenAt } }"
SET = """
mutation($purpose: ConsentPurpose!, $granted: Boolean!, $visitorId: String) {
  setConsent(purpose: $purpose, granted: $granted, language: DE, visitorId: $visitorId) {
    id purpose granted version givenAt
  }
}
"""
VISITOR = "6f1c9a8e-2d4b-4c1e-9a77-0b5e3c2d1f00"


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> None:
    caches["graphql_rate_limit"].clear()


@pytest.fixture
def policies() -> None:
    """The cookie policy and the privacy notice in force, which consents refer to."""
    publish_version(kind=LegalDocument.Kind.COOKIES, version="cookies-1")
    publish_version(kind=LegalDocument.Kind.PRIVACY, version="privacy-1")


def test_every_purpose_is_off_until_the_user_agrees(client: Client) -> None:
    user = make_user()
    token = sign_in(user).access_token

    found = gql(client, CONSENTS, token=token)["data"]["consents"]

    assert found == [
        {
            "id": f"user:{user.pk}:{purpose}",
            "purpose": purpose,
            "granted": False,
            "version": None,
            "givenAt": None,
        }
        for purpose in ("ANALYTICS", "MARKETING_EMAIL", "MARKETING_PUSH")
    ]


def test_signed_out_callers_cant_read_consents(client: Client) -> None:
    assert error_codes(gql(client, CONSENTS)) == ["UNAUTHENTICATED"]


@pytest.mark.usefixtures("policies")
def test_each_toggle_change_writes_a_consent_row_with_the_version(client: Client) -> None:
    user = make_user()
    token = sign_in(user, Session.Platform.ANDROID).access_token

    on = gql(client, SET, {"purpose": "MARKETING_EMAIL", "granted": True}, token=token)
    off = gql(client, SET, {"purpose": "MARKETING_EMAIL", "granted": False}, token=token)
    gql(client, SET, {"purpose": "ANALYTICS", "granted": True}, token=token)

    assert on["data"]["setConsent"]["granted"] is True
    assert on["data"]["setConsent"]["id"] == f"user:{user.pk}:MARKETING_EMAIL"
    assert on["data"]["setConsent"]["version"] == "privacy-1"
    assert off["data"]["setConsent"]["granted"] is False
    rows = list(Consent.objects.filter(user=user).order_by("given_at", "pk"))
    assert [(row.purpose, row.granted, row.version) for row in rows] == [
        ("MARKETING_EMAIL", True, "privacy-1"),
        ("MARKETING_EMAIL", False, "privacy-1"),
        ("ANALYTICS", True, "cookies-1"),
    ]
    assert {row.platform for row in rows} == {"android"}
    assert {row.language for row in rows} == {"de"}
    assert rows[2].document is not None and rows[2].document.kind == "COOKIES"
    state = {
        c["purpose"]: c["granted"] for c in gql(client, CONSENTS, token=token)["data"]["consents"]
    }
    assert state == {"ANALYTICS": True, "MARKETING_EMAIL": False, "MARKETING_PUSH": False}
    assert consents.has_consent(user, Consent.Purpose.ANALYTICS)
    assert not consents.has_consent(user, Consent.Purpose.MARKETING_EMAIL)
    assert not consents.has_consent(user, Consent.Purpose.MARKETING_PUSH)


@pytest.mark.usefixtures("policies")
def test_a_signed_out_visitor_logs_the_cookie_banner_choice(client: Client) -> None:
    body = gql(client, SET, {"purpose": "ANALYTICS", "granted": False, "visitorId": VISITOR})

    assert body["data"]["setConsent"]["granted"] is False
    # Its own cache id, so it never overwrites a signed-in account's state in the client.
    assert body["data"]["setConsent"]["id"] == f"visitor:{VISITOR}:ANALYTICS"
    row = Consent.objects.get()
    assert row.user is None
    assert row.visitor_id == uuid.UUID(VISITOR)
    assert (row.purpose, row.granted, row.version, row.platform) == (
        "ANALYTICS",
        False,
        "cookies-1",
        "web",
    )


@pytest.mark.usefixtures("policies")
def test_a_signed_out_caller_needs_a_visitor_id_and_can_only_answer_analytics(
    client: Client,
) -> None:
    no_visitor = gql(client, SET, {"purpose": "ANALYTICS", "granted": True})
    marketing = gql(
        client, SET, {"purpose": "MARKETING_EMAIL", "granted": True, "visitorId": VISITOR}
    )

    assert error_codes(no_visitor) == ["UNAUTHENTICATED"]
    assert error_codes(marketing) == ["UNAUTHENTICATED"]
    assert not Consent.objects.exists()


def test_consent_cant_be_given_while_no_policy_is_in_force(client: Client) -> None:
    token = sign_in(make_user()).access_token

    granted = gql(client, SET, {"purpose": "ANALYTICS", "granted": True}, token=token)
    refused = gql(client, SET, {"purpose": "ANALYTICS", "granted": False}, token=token)

    assert error_codes(granted) == ["CONSENT_UNAVAILABLE"]
    # Saying no is always recorded, with no version to refer to.
    assert refused["data"]["setConsent"]["granted"] is False
    assert refused["data"]["setConsent"]["version"] is None
    assert list(Consent.objects.values_list("granted", "version")) == [(False, "")]


@pytest.mark.usefixtures("policies")
def test_consent_rows_are_append_only() -> None:
    row = consents.record_consent(
        purpose=Consent.Purpose.ANALYTICS,
        granted=True,
        language="en",
        platform=Session.Platform.WEB,
        visitor_id=uuid.UUID(VISITOR),
    )

    row.granted = False
    with pytest.raises(ImmutableRowError):
        row.save()
    with pytest.raises(ImmutableRowError):
        Consent.objects.filter(pk=row.pk).update(granted=False)
    with pytest.raises(ImmutableRowError):
        row.delete()
    with pytest.raises(ImmutableRowError):
        Consent.objects.filter(pk=row.pk).delete()
    assert Consent.objects.filter(pk=row.pk).exists()


@pytest.mark.usefixtures("policies")
def test_consents_are_in_the_export_and_kept_detached_after_account_deletion() -> None:
    user = make_user()
    user_id = user.pk
    consents.record_consent(
        purpose=Consent.Purpose.MARKETING_PUSH,
        granted=True,
        language="hr",
        platform=Session.Platform.IOS,
        user=user,
    )

    exported = consents.export_consents(user)
    user.delete()

    assert [(c["purpose"], c["granted"], c["version"]) for c in exported] == [
        ("MARKETING_PUSH", True, "privacy-1")
    ]
    kept = Consent.objects.get()
    assert kept.user is None
    assert kept.user_reference == user_id
    assert (kept.purpose, kept.granted, kept.version) == ("MARKETING_PUSH", True, "privacy-1")


@pytest.mark.usefixtures("policies")
def test_set_consent_is_rate_limited(client: Client) -> None:
    for _ in range(10):
        gql(client, SET, {"purpose": "ANALYTICS", "granted": False, "visitorId": VISITOR})

    assert error_codes(
        gql(client, SET, {"purpose": "ANALYTICS", "granted": False, "visitorId": VISITOR})
    ) == ["RATE_LIMITED"]
