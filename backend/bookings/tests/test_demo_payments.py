"""Demo payments through the real booking code (BEAV-198): acceptOffer charges the demo
Stripe stand-in, the demo card form confirms the PaymentIntent, and only its
payment_intent.succeeded event, recorded in stripe_events and handled by the webhook
handler, makes the booking FUNDS_HELD (hard rule a). A cancellation is settled there and
confirmed by charge.refunded."""

from collections.abc import Iterator

import pytest
from django.test import Client, override_settings

from bookings import cancel, settlement
from bookings.models import Booking, BookingEvent
from bookings.tests.test_checkout import CANCELLATION, accept, two_offers
from core.models import ActorKind
from helpers.models import HelperProfile
from legal.models import LegalDocument
from legal.tests.factories import publish_version
from offers.models import Offer
from payments import demo_stripe, stripe_client, webhooks
from payments.models import ConnectedAccount, DemoStripeObject, StripeEvent
from payments.tests.demo import deliver, demo_payments, reload_urls  # noqa: F401

pytestmark = pytest.mark.django_db

S = Booking.Status


@pytest.fixture(autouse=True)
def demo(demo_payments: None) -> Iterator[None]:  # noqa: F811
    publish_version(LegalDocument.Kind.TERMS, "2026-11-t")
    publish_version(LegalDocument.Kind.CANCELLATION, CANCELLATION)
    with override_settings(DEMO_PAYMENTS=True):
        reload_urls()
        yield
    reload_urls()


def demo_offer() -> Offer:
    """An offer whose helper has an account at the demo stand-in."""
    offer, _ = two_offers()
    profile = HelperProfile.objects.get(user=offer.helper)
    account = stripe_client.get_gateway().create_express_account(
        helper_profile_id=profile.pk, idempotency_key=f"helper:{profile.pk}:express-account:v1"
    )
    ConnectedAccount.objects.filter(helper=profile).update(stripe_account_id=account.id)
    deliver()
    return offer


def confirm(client: Client, booking: Booking, secret: str) -> int:
    response = client.post(
        f"/demo-stripe/v1/payment_intents/{booking.payment_intent_id}/confirm",
        {"client_secret": secret, "card_number": "4242424242424242"},
        content_type="application/json",
    )
    return response.status_code


def test_the_demo_card_holds_the_money_only_through_the_webhook_event(client: Client) -> None:
    session = accept(demo_offer())
    booking = Booking.objects.get(pk=session.booking.pk)
    assert booking.payment_intent_id.startswith("pi_demo")

    assert confirm(client, booking, session.client_secret) == 200
    assert confirm(client, booking, session.client_secret) == 200

    # Confirmed at "Stripe", but nothing changes until the webhook event is handled.
    booking.refresh_from_db()
    assert booking.status == S.AWAITING_PAYMENT
    assert DemoStripeObject.objects.filter(object="event", delivered_at__isnull=True).count() == 1

    assert deliver() == 1
    booking.refresh_from_db()
    assert booking.status == S.FUNDS_HELD
    event = StripeEvent.objects.get(type="payment_intent.succeeded")
    assert event.processed_at is not None
    held = BookingEvent.objects.get(booking=booking, type="funds_held")
    assert held.actor_kind == ActorKind.STRIPE_WEBHOOK
    assert held.data["stripe_event"] == event.pk


def test_a_redelivered_demo_event_changes_nothing(client: Client) -> None:
    session = accept(demo_offer())
    booking = Booking.objects.get(pk=session.booking.pk)
    confirm(client, booking, session.client_secret)
    deliver()
    payload = StripeEvent.objects.get(type="payment_intent.succeeded").payload

    assert webhooks.process_event(payload) is False
    assert BookingEvent.objects.filter(booking=booking, type="funds_held").count() == 1


def test_the_charge_is_a_destination_charge_with_the_fee(client: Client) -> None:
    session = accept(demo_offer())
    booking = Booking.objects.get(pk=session.booking.pk)
    intent = DemoStripeObject.objects.get(pk=booking.payment_intent_id).data
    account = ConnectedAccount.objects.get(helper__user_id=booking.helper_id).stripe_account_id

    assert intent["amount"] == booking.amount_cents + booking.fee_cents
    assert intent["application_fee_amount"] == booking.fee_cents
    assert intent["on_behalf_of"] == account
    assert intent["transfer_data"] == {"destination": account}
    assert intent["metadata"] == {"booking_id": str(booking.pk)}


def test_a_cancellation_is_refunded_and_confirmed_by_charge_refunded(client: Client) -> None:
    session = accept(demo_offer())
    booking = Booking.objects.get(pk=session.booking.pk)
    confirm(client, booking, session.client_secret)
    deliver()
    poster = booking.poster

    cancel.cancel_booking(poster, booking.pk, cancel.PosterCancelReason.NO_LONGER_NEEDED)
    settlement.settle(str(booking.pk))
    booking.refresh_from_db()
    assert booking.status == S.CANCEL_REQUESTED

    deliver()
    booking.refresh_from_db()
    assert booking.status == S.REFUNDED
    charge = DemoStripeObject.objects.get(object="charge").data
    assert charge["amount_refunded"] == booking.amount_cents + booking.fee_cents
    assert StripeEvent.objects.get(type="charge.refunded").processed_at is not None
    assert demo_stripe.deliver_events() == 0
