"""The demo Stripe stand-in (payments/demo_stripe.py, BEAV-198): it answers the SDK's
requests the gateway makes, keeps idempotency keys as Stripe does, and reports through
webhook events that go through the real handler. Booking flows: bookings/tests/
test_demo_payments.py."""

from collections.abc import Iterator

import pytest
from django.test import override_settings
from django.urls import NoReverseMatch, reverse

from payments import demo_stripe, stripe_client
from payments.models import DemoStripeObject, StripeEvent
from payments.tests.demo import deliver, demo_payments, reload_urls  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def gateway(demo_payments: None) -> stripe_client.StripeGateway:  # noqa: F811
    return stripe_client.get_gateway()


def test_decode_params_reads_stripes_form_encoding() -> None:
    encoded = (
        "amount=3360&metadata[booking_id]=b1&transfer_data[destination]=acct_x"
        "&expand[0]=latest_charge&capabilities[card_payments][requested]=true"
    )

    assert demo_stripe.decode_params(encoded) == {
        "amount": "3360",
        "metadata": {"booking_id": "b1"},
        "transfer_data": {"destination": "acct_x"},
        "expand": ["latest_charge"],
        "capabilities": {"card_payments": {"requested": "true"}},
    }


def test_the_gateway_uses_the_stand_in_only_with_demo_payments(
    gateway: stripe_client.StripeGateway,
) -> None:
    account = gateway.create_express_account(
        helper_profile_id=1, idempotency_key="helper:1:express-account:v1"
    )

    assert account.id.startswith("acct_demo")
    assert DemoStripeObject.objects.filter(pk=account.id, object="account").exists()


@override_settings(DEMO_PAYMENTS=False, STRIPE_SECRET_KEY="")
def test_without_demo_payments_the_gateway_needs_a_real_key() -> None:
    stripe_client.get_gateway.cache_clear()
    try:
        with pytest.raises(Exception, match="STRIPE_SECRET_KEY"):
            stripe_client.get_gateway()
    finally:
        stripe_client.get_gateway.cache_clear()


def test_a_repeated_idempotency_key_gets_the_same_object(
    gateway: stripe_client.StripeGateway,
) -> None:
    account = gateway.create_express_account(
        helper_profile_id=1, idempotency_key="helper:1:express-account:v1"
    )
    first = charge(gateway, account.id)
    again = charge(gateway, account.id)

    assert again.id == first.id
    assert DemoStripeObject.objects.filter(object="payment_intent").count() == 1
    assert str(first.client_secret).startswith(f"{first.id}_secret_")


def test_a_charge_to_an_unknown_account_is_refused_like_stripe(
    gateway: stripe_client.StripeGateway,
) -> None:
    with pytest.raises(stripe_client.StripeRequestError) as refused:
        charge(gateway, "acct_none")

    assert refused.value.code == "account_invalid"
    assert refused.value.is_connected_account_problem


def test_a_new_account_is_enabled_through_its_account_updated_event(
    gateway: stripe_client.StripeGateway,
) -> None:
    account = gateway.create_express_account(
        helper_profile_id=7, idempotency_key="helper:7:express-account:v1"
    )

    assert deliver() == 1
    event = StripeEvent.objects.get(type="account.updated")
    assert event.payload["account"] == account.id
    assert event.payload["livemode"] is False
    assert deliver() == 0


def test_a_cancel_after_payment_carries_the_payment_intents_status(
    gateway: stripe_client.StripeGateway,
) -> None:
    account = gateway.create_express_account(
        helper_profile_id=1, idempotency_key="helper:1:express-account:v1"
    )
    intent = charge(gateway, account.id)
    demo_stripe.confirm_payment_intent(intent.id, str(intent.client_secret), "4242424242424242")

    with pytest.raises(stripe_client.StripeRequestError) as refused:
        gateway.cancel_payment_intent(
            booking_id="b1", payment_intent=intent.id, idempotency_key="booking:b1:cancel_pi:v1"
        )

    assert refused.value.payment_intent_status == "succeeded"


def test_a_payout_queues_payout_paid_for_the_connected_account(
    gateway: stripe_client.StripeGateway,
) -> None:
    account = gateway.create_express_account(
        helper_profile_id=1, idempotency_key="helper:1:express-account:v1"
    )
    payout = gateway.create_payout(
        booking_id=5,
        connected_account=account.id,
        amount_cents=3_000,
        idempotency_key="booking:5:release:v1",
    )

    deliver()
    event = StripeEvent.objects.get(type="payout.paid")
    assert event.payload["account"] == account.id
    assert event.payload["data"]["object"]["id"] == payout.id
    assert event.payload["request"]["idempotency_key"] == "booking:5:release:v1"


def test_the_confirm_route_is_mounted_only_with_demo_payments() -> None:
    try:
        with override_settings(DEMO_PAYMENTS=False):
            reload_urls()
            with pytest.raises(NoReverseMatch):
                reverse("demo-stripe-confirm", args=["pi_demo1"])
        with override_settings(DEMO_PAYMENTS=True):
            reload_urls()
            assert reverse("demo-stripe-confirm", args=["pi_demo1"]) == (
                "/demo-stripe/v1/payment_intents/pi_demo1/confirm"
            )
    finally:
        reload_urls()


@pytest.fixture
def mounted(demo_payments: None) -> Iterator[None]:  # noqa: F811
    with override_settings(DEMO_PAYMENTS=True):
        reload_urls()
        yield
    reload_urls()


def test_the_confirm_endpoint_answers_only_dev_web_origins(client, mounted: None) -> None:
    url = "/demo-stripe/v1/payment_intents/pi_demo1/confirm"

    allowed = client.options(url, headers={"Origin": "http://localhost:3000"})
    other = client.options(url, headers={"Origin": "https://evil.example.com"})

    assert allowed.status_code == 204
    assert allowed["Access-Control-Allow-Origin"] == "http://localhost:3000"
    assert "Access-Control-Allow-Origin" not in other


def test_the_confirm_endpoint_needs_the_client_secret(
    client, mounted: None, gateway: stripe_client.StripeGateway
) -> None:
    account = gateway.create_express_account(
        helper_profile_id=1, idempotency_key="helper:1:express-account:v1"
    )
    intent = charge(gateway, account.id)
    url = f"/demo-stripe/v1/payment_intents/{intent.id}/confirm"

    wrong = client.post(url, {"client_secret": "nope"}, content_type="application/json")
    missing = client.post(url, {}, content_type="application/json")

    assert wrong.status_code == 404
    assert missing.status_code == 400
    assert not DemoStripeObject.objects.filter(
        object="event", data__type__startswith="payment_intent."
    ).exists()


def test_a_declined_demo_card_queues_payment_failed(gateway: stripe_client.StripeGateway) -> None:
    account = gateway.create_express_account(
        helper_profile_id=1, idempotency_key="helper:1:express-account:v1"
    )
    deliver()
    intent = charge(gateway, account.id)

    answer = demo_stripe.confirm_payment_intent(
        intent.id, str(intent.client_secret), demo_stripe.DECLINED_CARD
    )

    assert answer["status"] == "requires_payment_method"
    assert answer["last_payment_error"]["decline_code"] == "generic_decline"
    deliver()
    assert StripeEvent.objects.filter(type="payment_intent.payment_failed").count() == 1


@override_settings(DEMO_PAYMENTS=False)
def test_events_are_never_delivered_without_demo_payments() -> None:
    DemoStripeObject.objects.create(
        id="evt_demo1", object="event", data={"id": "evt_demo1", "type": "account.updated"}
    )

    assert deliver() == 0
    assert not StripeEvent.objects.exists()


def charge(gateway: stripe_client.StripeGateway, account: str):
    return gateway.create_destination_charge(
        booking_id="b1",
        amount_cents=3_360,
        application_fee_cents=360,
        destination_account=account,
        idempotency_key="booking:b1:payment_intent:v1",
    )
