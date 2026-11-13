"""The public currentLegalDocument query, over HTTP at /graphql."""

import datetime as dt
from typing import Any

import pytest
from django.test import Client
from django.utils import timezone

from legal.models import LegalDocumentWithdrawal
from legal.tests.factories import publish_version

pytestmark = pytest.mark.django_db

CURRENT = """
query Current($kind: LegalDocumentKind!, $language: Locale!) {
  currentLegalDocument(kind: $kind, language: $language) {
    id kind version language effectiveAt
  }
}
"""


def current(client: Client, kind: str = "TERMS", language: str = "DE") -> Any:
    response = client.post(
        "/graphql",
        {"query": CURRENT, "variables": {"kind": kind, "language": language}},
        content_type="application/json",
    )
    assert response.status_code == 200
    body = response.json()
    assert "errors" not in body
    return body["data"]["currentLegalDocument"]


def test_signed_out_clients_get_the_version_in_force_in_their_language(client: Client) -> None:
    documents = publish_version(version="2026-11")
    publish_version(version="2027-01", effective_at=timezone.now() + dt.timedelta(days=40))
    german = next(document for document in documents if document.language == "de")

    assert current(client) == {
        "id": str(german.pk),
        "kind": "TERMS",
        "version": "2026-11",
        "language": "DE",
        "effectiveAt": german.effective_at.isoformat(),
    }


def test_null_while_no_version_is_in_force(client: Client) -> None:
    assert current(client) is None
    publish_version(version="2026-11")
    LegalDocumentWithdrawal.objects.create(kind="TERMS", version="2026-11", reason="test")

    assert current(client) is None
    assert current(client, kind="PRIVACY") is None


CHECKOUT = """
query Current($language: Locale!) {
  currentLegalDocument(kind: CANCELLATION, language: $language) {
    id version text checkoutConsent {
      privateHelper { cancellationPolicy earlyStart }
      traderHelper { cancellationPolicy earlyStart }
    }
  }
}
"""


def test_a_cancellation_policy_carries_its_checkout_tick_box_texts(client: Client) -> None:
    publish_version("CANCELLATION", "2026-11-c")

    response = client.post(
        "/graphql",
        {"query": CHECKOUT, "variables": {"language": "FR"}},
        content_type="application/json",
    )

    document = response.json()["data"]["currentLegalDocument"]
    assert document["checkoutConsent"] == {
        "privateHelper": {
            "cancellationPolicy": "I accept the policy (private, fr).",
            "earlyStart": "Start early, please (private, fr).",
        },
        "traderHelper": {
            "cancellationPolicy": "I accept the policy (trader, fr).",
            "earlyStart": "Start early, please (trader, fr).",
        },
    }
    # The markers never reach the reader; their sentences do.
    assert "checkout:" not in document["text"]
    assert "Start early, please (trader, fr)." in document["text"]


def test_other_documents_have_no_checkout_texts(client: Client) -> None:
    publish_version(version="2026-11")

    response = client.post(
        "/graphql",
        {
            "query": "{ currentLegalDocument(kind: TERMS, language: HR) { text checkoutConsent "
            "{ privateHelper { earlyStart } } } }"
        },
        content_type="application/json",
    )

    assert response.json()["data"]["currentLegalDocument"] == {
        "text": "Terms (hr)",
        "checkoutConsent": None,
    }


PAGE = """
query Page($kind: LegalDocumentKind!, $language: Locale!, $version: String!) {
  legalDocument(kind: $kind, language: $language, version: $version) {
    version language isDraft text
  }
  legalDocumentVersions(kind: $kind, language: $language) { version effectiveAt isDraft }
}
"""


def page(client: Client, version: str, kind: str = "TERMS", language: str = "EN") -> Any:
    response = client.post(
        "/graphql",
        {"query": PAGE, "variables": {"kind": kind, "language": language, "version": version}},
        content_type="application/json",
    )
    assert response.status_code == 200
    body = response.json()
    assert "errors" not in body
    return body["data"]


def test_any_published_version_can_be_read_and_versions_list_newest_first(
    client: Client,
) -> None:
    now = timezone.now()
    publish_version(
        version="v1", effective_at=now - dt.timedelta(days=60), now=now - dt.timedelta(days=61)
    )
    publish_version(version="v2", now=now)
    scheduled = publish_version(version="v3", effective_at=now + dt.timedelta(days=40), now=now)

    data = page(client, "v1")

    assert data["legalDocument"] == {
        "version": "v1",
        "language": "EN",
        "isDraft": False,
        "text": "Terms (en)",
    }
    # Scheduled versions are listed too, so a 30-day notice can link to the coming text.
    assert [row["version"] for row in data["legalDocumentVersions"]] == ["v3", "v2", "v1"]
    assert data["legalDocumentVersions"][0]["effectiveAt"] == scheduled[0].effective_at.isoformat()


def test_unknown_and_withdrawn_versions_are_null(client: Client) -> None:
    publish_version(version="v1")
    publish_version(version="v2", effective_at=timezone.now() + dt.timedelta(days=40))
    LegalDocumentWithdrawal.objects.create(kind="TERMS", version="v2", reason="test")

    assert page(client, "nope")["legalDocument"] is None
    data = page(client, "v2")
    assert data["legalDocument"] is None
    assert [row["version"] for row in data["legalDocumentVersions"]] == ["v1"]


def test_drafts_are_marked_and_never_served_where_drafts_are_not_allowed(
    client: Client, settings: Any
) -> None:
    publish_version(version="draft-1", is_draft=True)

    assert page(client, "draft-1")["legalDocument"]["isDraft"] is True

    settings.LEGAL_DRAFTS_ALLOWED = False
    data = page(client, "draft-1")
    assert data == {"legalDocument": None, "legalDocumentVersions": []}
    assert current(client) is None


def test_geolite2_attribution_follows_a_readable_database(
    client: Client, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    query = {"query": "{ usesGeolite2 }"}
    settings.GEOIP_CITY_DATABASE = ""
    assert client.post("/graphql", query, content_type="application/json").json() == {
        "data": {"usesGeolite2": False}
    }

    settings.GEOIP_CITY_DATABASE = "/configured/GeoLite2-City.mmdb"
    settings.MAXMIND_ACCOUNT_ID = "123"
    settings.MAXMIND_LICENSE_KEY = "secret"
    # Configured is enough: this instance may still be downloading its first copy.
    response = client.post("/graphql", query, content_type="application/json")
    assert response.json() == {"data": {"usesGeolite2": True}}
