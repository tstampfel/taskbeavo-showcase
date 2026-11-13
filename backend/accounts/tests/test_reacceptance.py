"""Accepting updated terms: the way out of TERMS_UPDATE_REQUIRED (plan: "When the terms
change"; BEAV-82). The "We've updated our terms" screen calls acceptCurrentTerms."""

import datetime as dt
from typing import Any

import pytest
from django.core.cache import caches
from django.test import Client
from django.utils import timezone

from accounts import states
from accounts.models import User
from accounts.states import AccountState
from accounts.tests.factories import access_token, make_user, sign_in
from accounts.tests.test_schema import error_codes, gql
from accounts.tests.test_states import accept, publish_scheduled
from core.errors import ApiError
from legal.models import LegalDocument, TermsAcceptance
from legal.services import withdraw
from legal.tests.factories import publish_version

pytestmark = pytest.mark.django_db

TERMS = LegalDocument.Kind.TERMS
ACCEPT = """
mutation($version: String!, $language: Locale!) {
  acceptCurrentTerms(version: $version, language: $language) {
    id accountState mustAcceptTerms
  }
}
"""
VIEWER = "{ viewer { id accountState mustAcceptTerms } }"


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> None:
    caches["graphql_rate_limit"].clear()


@pytest.fixture
def ana() -> User:
    """Accepted v1; the material v2 took effect yesterday, so she is in limited mode."""
    publish_scheduled("v1", days=80, material=False)
    user = make_user("ana", phone_verified_at=timezone.now())
    accept(user, "v1")
    publish_scheduled("v2", days=1, material=True)
    assert states.account_state(user) is AccountState.TERMS_UPDATE_REQUIRED
    return user


def code_of(exc: pytest.ExceptionInfo[ApiError]) -> str:
    return exc.value.code.name


# --- The service ----------------------------------------------------------------------


def test_accepting_the_version_in_force_ends_limited_mode(ana: User) -> None:
    states.accept_current_terms(ana, "v2", "de", "ios")

    assert states.account_state(ana) is AccountState.ACTIVE
    row = TermsAcceptance.objects.get(user=ana, document__version="v2")
    # The version and language shown, and where: the proof (plan: "How acceptance works").
    assert (row.document.kind, row.document.language, row.platform) == (TERMS, "de", "ios")
    # Only sign-up records the sign-in method.
    assert row.sign_in_method == ""
    assert row.booking is None


def test_accepting_twice_records_one_acceptance(ana: User) -> None:
    states.accept_current_terms(ana, "v2", "de", "ios")
    states.accept_current_terms(ana, "v2", "fr", "web")

    assert TermsAcceptance.objects.filter(user=ana, document__version="v2").count() == 1


def test_an_old_version_is_refused(ana: User) -> None:
    with pytest.raises(ApiError) as exc:
        states.accept_current_terms(ana, "v1", "en", "web")

    assert code_of(exc) == "TERMS_NOT_ACCEPTED"
    assert states.must_accept_terms(ana)


def test_a_scheduled_version_is_refused(ana: User) -> None:
    """The screen accepts what is in force; a version still waiting out its notice can be
    withdrawn, and accepting it early wouldn't count (review BEAV-77 R1-4)."""
    publish_version(
        version="v3", effective_at=timezone.now() + dt.timedelta(days=31), material_change=True
    )

    with pytest.raises(ApiError) as exc:
        states.accept_current_terms(ana, "v3", "en", "web")

    assert code_of(exc) == "TERMS_NOT_ACCEPTED"
    assert not TermsAcceptance.objects.filter(document__version="v3").exists()


def test_an_unknown_version_is_refused(ana: User) -> None:
    with pytest.raises(ApiError) as exc:
        states.accept_current_terms(ana, "v9", "en", "web")

    assert code_of(exc) == "TERMS_NOT_ACCEPTED"


def test_a_withdrawn_version_is_skipped_for_the_one_before() -> None:
    """v2 was withdrawn before its date: v1 stays in force and is what to accept."""
    publish_scheduled("v1", days=80, material=False)
    publish_version(
        version="v2", effective_at=timezone.now() + dt.timedelta(days=31), material_change=True
    )
    withdraw(TERMS, "v2", reason="Wrong text")
    ivo = make_user("ivo")

    with pytest.raises(ApiError):
        states.accept_current_terms(ivo, "v2", "en", "web")
    states.accept_current_terms(ivo, "v1", "en", "web")

    assert states.account_state(ivo) is AccountState.ACTIVE


def test_nothing_to_accept_while_no_terms_are_in_force() -> None:
    with pytest.raises(ApiError) as exc:
        states.accept_current_terms(make_user("ivo"), "v1", "en", "web")

    assert code_of(exc) == "TERMS_NOT_ACCEPTED"


def test_a_small_fix_in_force_is_accepted_as_itself(ana: User) -> None:
    """A typo fix after the material v2: accepting the fix counts for v2 too."""
    now = timezone.now()
    publish_version(version="v2.1", now=now, effective_at=now, material_change=False)

    states.accept_current_terms(ana, "v2.1", "hr", "android")

    assert states.account_state(ana) is AccountState.ACTIVE


def test_a_suspended_account_can_accept_but_stays_suspended(ana: User) -> None:
    User.objects.filter(pk=ana.pk).update(status=User.Status.SUSPENDED)
    ana.refresh_from_db()

    states.accept_current_terms(ana, "v2", "en", "web")

    assert not states.must_accept_terms(ana)
    assert states.account_state(ana) is AccountState.SUSPENDED


# --- Over GraphQL --------------------------------------------------------------------


def call(client: Client, token: str | None, **variables: Any) -> dict[str, Any]:
    return gql(client, ACCEPT, {"version": "v2", "language": "DE", **variables}, token=token)


def test_the_viewer_reports_must_accept_terms(client: Client, ana: User) -> None:
    body = gql(client, VIEWER, token=access_token(ana))

    assert body["data"]["viewer"] == {
        "id": str(ana.pk),
        "accountState": "TERMS_UPDATE_REQUIRED",
        "mustAcceptTerms": True,
    }


def test_must_accept_terms_is_false_once_accepted(client: Client) -> None:
    publish_scheduled("v1", days=80, material=False)
    ivo = make_user("ivo")
    accept(ivo, "v1")

    body = gql(client, VIEWER, token=access_token(ivo))

    assert body["data"]["viewer"]["mustAcceptTerms"] is False


def test_must_accept_terms_shows_through_a_suspension(client: Client, ana: User) -> None:
    """The state says SUSPENDED, but the screen still asks for the terms."""
    User.objects.filter(pk=ana.pk).update(status=User.Status.SUSPENDED)

    body = gql(client, VIEWER, token=access_token(ana))

    assert body["data"]["viewer"]["accountState"] == "SUSPENDED"
    assert body["data"]["viewer"]["mustAcceptTerms"] is True


def test_accepting_returns_the_full_viewer(client: Client, ana: User) -> None:
    body = call(client, sign_in(ana, platform="android").access_token)

    assert body["data"]["acceptCurrentTerms"] == {
        "id": str(ana.pk),
        "accountState": "ACTIVE",
        "mustAcceptTerms": False,
    }
    row = TermsAcceptance.objects.get(user=ana, document__version="v2")
    # The platform comes from the session, not from the client's word.
    assert (row.document.language, row.platform) == ("de", "android")


def test_accepting_needs_sign_in(client: Client, ana: User) -> None:
    assert error_codes(call(client, None)) == ["UNAUTHENTICATED"]
    assert not TermsAcceptance.objects.filter(document__version="v2").exists()


def test_accepting_a_version_not_in_force_is_refused(client: Client, ana: User) -> None:
    body = call(client, access_token(ana), version="v1")

    assert error_codes(body) == ["TERMS_NOT_ACCEPTED"]


def test_accepting_is_rate_limited(client: Client, ana: User, settings: Any) -> None:
    settings.GRAPHQL_MUTATION_RATE_LIMIT_OVERRIDES = {"acceptCurrentTerms": 2}
    token = access_token(ana)

    answers = [error_codes(call(client, token)) for _ in range(3)]

    assert answers == [[], [], ["RATE_LIMITED"]]
    assert TermsAcceptance.objects.filter(user=ana, document__version="v2").count() == 1


# --- What the screen shows: the version and date it records (BEAV-188) ------------------

TO_ACCEPT = """
query($language: Locale!) {
  viewer { id termsToAccept(language: $language) { kind version language effectiveAt } }
}
"""


def test_the_tick_box_shows_the_version_and_date_accepting_records(
    client: Client, ana: User
) -> None:
    """The agree line names `termsToAccept`'s version and effective date; accepting that
    version records exactly that document, so the record proves which text was known."""
    token = access_token(ana)

    shown = gql(client, TO_ACCEPT, {"language": "DE"}, token=token)["data"]["viewer"]
    accepted = call(client, token, version=shown["termsToAccept"]["version"])

    assert shown["termsToAccept"]["kind"] == "TERMS"
    assert shown["termsToAccept"]["language"] == "DE"
    assert accepted.get("errors") is None
    row = TermsAcceptance.objects.filter(user=ana).latest("accepted_at")
    assert row.document.version == shown["termsToAccept"]["version"] == "v2"
    assert row.document.effective_at == dt.datetime.fromisoformat(
        shown["termsToAccept"]["effectiveAt"]
    )


def test_nothing_to_accept_once_accepted(client: Client, ana: User) -> None:
    token = access_token(ana)
    call(client, token)

    body = gql(client, TO_ACCEPT, {"language": "EN"}, token=token)

    assert body["data"]["viewer"]["termsToAccept"] is None


def test_terms_to_accept_is_null_without_a_terms_change(client: Client) -> None:
    publish_scheduled("v1", days=80, material=False)
    ivo = make_user("ivo")
    accept(ivo, "v1")

    body = gql(client, TO_ACCEPT, {"language": "HR"}, token=access_token(ivo))

    assert body["data"]["viewer"]["termsToAccept"] is None
