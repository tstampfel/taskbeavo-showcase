"""Accepting an offer when the helper's account is limited (accounts.states; user decision
2026-11-04, review BEAV-77 R1-2)."""

import datetime as dt

import pytest
from django.utils import timezone

from accounts.models import User
from accounts.tests.factories import accept_current_terms
from bookings import checkout
from bookings.models import Booking
from bookings.tests.factories import checkout_fee_version, checkout_total
from bookings.tests.fakes import FakeGateway
from core.errors import ApiError, ErrorCode
from legal.models import LegalDocument
from legal.tests.factories import publish_version
from offers import services as offers
from offers.models import Offer
from offers.tests.factories import make_helper, open_task

pytestmark = pytest.mark.django_db

CANCELLATION = "2026-11-c"


@pytest.fixture(autouse=True)
def legal_documents() -> None:
    now = timezone.now()
    publish_version(
        LegalDocument.Kind.TERMS,
        "2026-11-t",
        now=now - dt.timedelta(days=81),
        effective_at=now - dt.timedelta(days=80),
    )
    publish_version(LegalDocument.Kind.CANCELLATION, CANCELLATION)


def an_offer() -> Offer:
    return offers.send_offer(make_helper("marko"), str(open_task().pk), 3_000)


def accept(offer: Offer) -> checkout.CheckoutSession:
    return checkout.accept_offer(
        offer.task.poster,
        str(offer.pk),
        expected_price_cents=offer.price_cents,
        expected_total_cents=checkout_total(offer.price_cents),
        expected_fee_schedule_version=checkout_fee_version(),
        consent=checkout.CheckoutConsent(
            cancellation_policy_accepted=True,
            early_start_requested=True,
            cancellation_version=CANCELLATION,
            platform="ios",
            language="de",
            variant="private",
        ),
    )


@pytest.mark.parametrize("status", [User.Status.SUSPENDED, User.Status.DELETION_PENDING])
def test_a_limited_helpers_offer_cant_be_accepted(gateway: FakeGateway, status: str) -> None:
    offer = an_offer()
    User.objects.filter(pk=offer.helper_id).update(status=status)
    offer = Offer.objects.get(pk=offer.pk)

    with pytest.raises(ApiError) as exc:
        accept(offer)

    # The poster is told only that the offer isn't available, never why.
    assert exc.value.extensions["code"] == ErrorCode.OFFER_NOT_AVAILABLE.name
    assert not Booking.objects.exists()
    assert gateway.charges == []


def test_a_helper_who_must_accept_new_terms_can_still_be_booked(gateway: FakeGateway) -> None:
    offer = an_offer()
    # A material version takes effect that the helper hasn't accepted; the poster has.
    now = timezone.now()
    publish_version(
        version="2027-01",
        now=now - dt.timedelta(days=32),
        effective_at=now - dt.timedelta(days=1),
        material_change=True,
    )
    offer = Offer.objects.get(pk=offer.pk)
    accept_current_terms(offer.task.poster)

    session = accept(offer)

    assert session.booking.helper_id == offer.helper_id
