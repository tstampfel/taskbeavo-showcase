"""A material terms change over HTTP, end to end (BEAV-82): after its effective date an
account that hasn't accepted it can't post, offer or book; once it accepts on "We've
updated our terms" it can again. A booking made under v1 keeps reporting v1."""

import datetime as dt
from typing import Any

import pytest
from django.core.cache import caches
from django.test import Client
from django.utils import timezone

from accounts.models import User
from accounts.tests.factories import accept_current_terms, sign_in
from accounts.tests.test_schema import error_codes, gql
from bookings.models import Booking
from bookings.tests.fakes import FakeGateway
from bookings.tests.test_checkout import ACCEPT as ACCEPT_OFFER
from bookings.tests.test_checkout import CANCELLATION, accept, consent_input
from legal.models import LegalDocument
from legal.tests.factories import publish_version
from offers import services as offers
from offers.tests.factories import make_helper, open_task
from offers.tests.test_schema import SEND as SEND_OFFER
from tasks.tests.test_schema import POST as POST_TASK
from tasks.tests.test_schema import task_input

pytestmark = pytest.mark.django_db

V1, V2 = "2026-09", "2026-12"
ACCEPT_TERMS = """
mutation($version: String!, $language: Locale!) {
  acceptCurrentTerms(version: $version, language: $language) { id mustAcceptTerms }
}
"""
BOOKING = "query($id: ID!) { booking(id: $id) { id termsVersion } }"


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> None:
    caches["graphql_rate_limit"].clear()


@pytest.fixture(autouse=True)
def legal_documents() -> None:
    now = timezone.now()
    publish_version(
        LegalDocument.Kind.TERMS,
        V1,
        now=now - dt.timedelta(days=81),
        effective_at=now - dt.timedelta(days=80),
    )
    publish_version(LegalDocument.Kind.CANCELLATION, CANCELLATION)


def take_effect_v2() -> None:
    """The material v2, published with its 30 days' notice, took effect yesterday."""
    now = timezone.now()
    publish_version(
        LegalDocument.Kind.TERMS,
        V2,
        now=now - dt.timedelta(days=32),
        effective_at=now - dt.timedelta(days=1),
        material_change=True,
    )


def mutate(client: Client, user: User, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    return gql(client, query, variables, token=sign_in(user).access_token)


def accept_terms(client: Client, user: User) -> dict[str, Any]:
    return mutate(client, user, ACCEPT_TERMS, {"version": V2, "language": "HR"})


def test_after_the_effective_date_only_accepting_unlocks_new_work(
    client: Client, gateway: FakeGateway
) -> None:
    """The legal-terms limited mode, through the real mutations; checkout is not a way
    out (user decision 2026-11-05)."""
    task = open_task()
    poster = task.poster
    marko = make_helper("marko")
    accept_current_terms(marko)
    offer = offers.send_offer(marko, str(task.pk), 3_000)
    other_task = open_task()
    take_effect_v2()

    def post() -> dict[str, Any]:
        return mutate(client, poster, POST_TASK, {"input": task_input()})

    def send() -> dict[str, Any]:
        return mutate(
            client,
            marko,
            SEND_OFFER,
            {"input": {"taskId": str(other_task.pk), "priceCents": 3_000}},
        )

    def book() -> dict[str, Any]:
        variables = {
            "offerId": str(offer.pk),
            "price": 3_000,
            "total": 3_360,
            "consent": consent_input(),
        }
        return mutate(client, poster, ACCEPT_OFFER, variables)

    assert error_codes(post()) == ["TERMS_UPDATE_REQUIRED"]
    assert error_codes(send()) == ["TERMS_UPDATE_REQUIRED"]
    assert error_codes(book()) == ["TERMS_UPDATE_REQUIRED"]
    assert gateway.charges == []

    assert accept_terms(client, poster)["data"]["acceptCurrentTerms"]["mustAcceptTerms"] is False
    assert accept_terms(client, marko)["data"]["acceptCurrentTerms"]["mustAcceptTerms"] is False

    assert error_codes(post()) == []
    assert error_codes(send()) == []
    booked = book()
    assert error_codes(booked) == []
    assert booked["data"]["acceptOffer"]["booking"]["termsVersion"] == V2


def test_a_booking_made_under_v1_still_reports_v1(client: Client, gateway: FakeGateway) -> None:
    task = open_task()
    offer = offers.send_offer(make_helper("marko"), str(task.pk), 3_000)
    booking = accept(offer).booking
    take_effect_v2()
    accept_terms(client, task.poster)

    body = mutate(client, task.poster, BOOKING, {"id": str(booking.pk)})

    assert body["data"]["booking"] == {"id": str(booking.pk), "termsVersion": V1}
    assert Booking.objects.get(pk=booking.pk).terms_version == V1
