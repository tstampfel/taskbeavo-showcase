"""The checkout screens' API (BEAV-111): `booking` for its poster and helper, the card
attempts and expiry the poster's screen shows, `checkoutSession` to come back to an open
checkout on the same PaymentIntent, and `bookingUpdated` over the WebSocket."""

import asyncio
import datetime as dt
from typing import Any

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AbstractBaseUser
from django.core.cache import caches
from django.test import Client
from django.utils import timezone

from accounts.tests.factories import access_token, make_user, sign_in
from accounts.tests.test_schema import error_codes, gql
from bookings import checkout
from bookings.models import Booking, BookingEvent
from bookings.tests.fakes import FakeGateway, intent_id
from bookings.tests.test_checkout import accept, two_offers
from core.models import Outbox
from core.realtime import send_to_users, user_group
from legal.models import LegalDocument
from legal.tests.factories import publish_version
from offers import services as offers
from payments.stripe_client import StripeRequestError

S = Booking.Status

BOOKING = """
query($id: ID!) {
  booking(id: $id) {
    id status helperUserId helperIsTrader checkoutExpiresAt checkoutFailureReason
    lastPaymentAttempt { id kind declineCode at }
  }
}
"""

SESSION = """
query($id: ID!) {
  checkoutSession(bookingId: $id) { id clientSecret paymentPending booking { id status } }
}
"""


@pytest.fixture(autouse=True)
def legal_documents(request: pytest.FixtureRequest) -> None:
    if "django_db" in request.keywords:
        publish_version(LegalDocument.Kind.TERMS, "2026-11-t")
        publish_version(LegalDocument.Kind.CANCELLATION, "2026-11-c")


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> None:
    caches["graphql_rate_limit"].clear()


def declined(booking: Booking, code: str = "insufficient_funds", event: str = "evt_1") -> None:
    checkout.record_failed_attempt(
        {
            "id": booking.payment_intent_id,
            "metadata": {"booking_id": str(booking.pk)},
            "last_payment_error": {"decline_code": code, "message": "Card holder Ana K."},
        },
        stripe_event_id=event,
    )


def authentication_started(booking: Booking, event: str = "evt_3ds") -> None:
    checkout.record_action_required(
        {"id": booking.payment_intent_id, "metadata": {"booking_id": str(booking.pk)}},
        stripe_event_id=event,
    )


def opened(gateway: FakeGateway) -> Booking:
    chosen, _ = two_offers()
    return accept(chosen).booking


def query(client: Client, text: str, booking: Booking, user: Any) -> dict[str, Any]:
    return gql(client, text, {"id": str(booking.pk)}, token=sign_in(user).access_token)


def moved_back(booking: Booking, minutes: int) -> None:
    """The checkout opened `minutes` ago."""
    Booking.objects.filter(pk=booking.pk).update(
        status_changed_at=timezone.now() - dt.timedelta(minutes=minutes)
    )


# The booking query.


@pytest.mark.django_db
def test_the_poster_and_the_helper_see_the_booking(client: Client, gateway: FakeGateway) -> None:
    booking = opened(gateway)

    for user in (booking.poster, booking.helper):
        data = query(client, BOOKING, booking, user)["data"]["booking"]
        assert (data["id"], data["status"]) == (str(booking.pk), "AWAITING_PAYMENT")
        assert data["helperUserId"] == str(booking.helper_id)
        assert data["helperIsTrader"] is False


@pytest.mark.django_db
def test_nobody_else_sees_the_booking(client: Client, gateway: FakeGateway) -> None:
    booking = opened(gateway)

    stranger = query(client, BOOKING, booking, make_user("stranger"))
    signed_out = gql(client, BOOKING, {"id": str(booking.pk)})
    malformed = gql(client, BOOKING, {"id": "nope"}, token=sign_in(booking.poster).access_token)

    assert error_codes(stranger) == ["BOOKING_NOT_FOUND"]
    assert error_codes(signed_out) == ["UNAUTHENTICATED"]
    assert error_codes(malformed) == ["BOOKING_NOT_FOUND"]


@pytest.mark.django_db
def test_the_checkout_ends_30_minutes_after_accepting(client: Client, gateway: FakeGateway) -> None:
    booking = opened(gateway)

    data = query(client, BOOKING, booking, booking.poster)["data"]["booking"]

    expires = dt.datetime.fromisoformat(data["checkoutExpiresAt"])
    assert expires == booking.status_changed_at + dt.timedelta(minutes=30)
    assert data["lastPaymentAttempt"] is None


@pytest.mark.django_db
def test_a_decline_shows_its_code_to_the_poster_only_and_restarts_the_clock(
    client: Client, gateway: FakeGateway
) -> None:
    booking = opened(gateway)
    moved_back(booking, 20)
    declined(booking)
    event = BookingEvent.objects.get(booking=booking, type=checkout.PAYMENT_ATTEMPT_FAILED)

    poster = query(client, BOOKING, booking, booking.poster)["data"]["booking"]
    helper = query(client, BOOKING, booking, booking.helper)["data"]["booking"]

    assert poster["lastPaymentAttempt"] == {
        "id": str(event.pk),
        "kind": "DECLINED",
        "declineCode": "insufficient_funds",
        "at": event.created_at.isoformat(),
    }
    assert dt.datetime.fromisoformat(poster["checkoutExpiresAt"]) == (
        event.created_at + dt.timedelta(minutes=30)
    )
    assert helper["lastPaymentAttempt"] is None


@pytest.mark.django_db
def test_a_3d_secure_start_is_the_last_attempt(client: Client, gateway: FakeGateway) -> None:
    booking = opened(gateway)
    declined(booking)
    authentication_started(booking)

    attempt = query(client, BOOKING, booking, booking.poster)["data"]["booking"][
        "lastPaymentAttempt"
    ]

    assert (attempt["kind"], attempt["declineCode"]) == ("AUTHENTICATION_STARTED", None)


@pytest.mark.django_db
def test_attempts_never_push_the_checkout_past_60_minutes(gateway: FakeGateway) -> None:
    booking = opened(gateway)
    moved_back(booking, 50)
    declined(booking)
    booking.refresh_from_db()

    assert checkout.checkout_expires_at(booking) == booking.status_changed_at + dt.timedelta(
        minutes=60
    )


@pytest.mark.django_db
def test_an_expired_checkout_says_why(client: Client, gateway: FakeGateway) -> None:
    booking = opened(gateway)
    moved_back(booking, 31)
    checkout.expire_checkout(str(booking.pk))

    data = query(client, BOOKING, booking, booking.poster)["data"]["booking"]

    assert (data["status"], data["checkoutFailureReason"]) == ("PAYMENT_FAILED", "CHECKOUT_EXPIRED")
    assert data["checkoutExpiresAt"] is None


@pytest.mark.django_db
def test_a_withdrawn_offer_says_why(client: Client, gateway: FakeGateway) -> None:
    booking = opened(gateway)
    offers.withdraw_offer(booking.helper, str(booking.offer_id))

    data = query(client, BOOKING, booking, booking.poster)["data"]["booking"]

    assert (data["status"], data["checkoutFailureReason"]) == ("PAYMENT_FAILED", "OFFER_WITHDRAWN")


# checkoutSession: back to the open checkout.


@pytest.mark.django_db
def test_the_poster_gets_the_same_payment_intent_again(
    client: Client, gateway: FakeGateway
) -> None:
    booking = opened(gateway)
    declined(booking)

    session = query(client, SESSION, booking, booking.poster)["data"]["checkoutSession"]

    assert session["clientSecret"] == f"{intent_id(str(booking.pk))}_secret_test"
    assert session["paymentPending"] is False
    assert session["booking"] == {"id": str(booking.pk), "status": "AWAITING_PAYMENT"}
    assert gateway.retrieves == [booking.payment_intent_id]
    # Nothing new is created at Stripe: one PaymentIntent per booking.
    assert len(gateway.charges) == 1


@pytest.mark.django_db
def test_only_the_poster_gets_the_client_secret(client: Client, gateway: FakeGateway) -> None:
    booking = opened(gateway)

    helper = query(client, SESSION, booking, booking.helper)
    stranger = query(client, SESSION, booking, make_user("stranger"))
    signed_out = gql(client, SESSION, {"id": str(booking.pk)})

    assert error_codes(helper) == ["BOOKING_NOT_FOUND"]
    assert error_codes(stranger) == ["BOOKING_NOT_FOUND"]
    assert error_codes(signed_out) == ["UNAUTHENTICATED"]
    assert gateway.retrieves == []


@pytest.mark.django_db
def test_checkout_session_is_rate_limited_per_account(
    client: Client, gateway: FakeGateway, settings: Any
) -> None:
    """Each call reads from Stripe (review R1-3), so it counts like a mutation."""
    caches["graphql_rate_limit"].clear()
    settings.GRAPHQL_QUERY_RATE_LIMITS = {"checkoutSession": 2}
    booking = opened(gateway)
    token = sign_in(booking.poster).access_token

    answers = [gql(client, SESSION, {"id": str(booking.pk)}, token=token) for _ in range(3)]

    assert [error_codes(a) for a in answers] == [[], [], ["RATE_LIMITED"]]
    assert len(gateway.retrieves) == 2


@pytest.mark.django_db
def test_checkout_session_cant_be_multiplied_with_aliases(
    client: Client, gateway: FakeGateway
) -> None:
    caches["graphql_rate_limit"].clear()
    booking = opened(gateway)
    aliased = (
        "query($id: ID!) { a: checkoutSession(bookingId: $id) { id } "
        "b: checkoutSession(bookingId: $id) { id } }"
    )

    body = gql(client, aliased, {"id": str(booking.pk)}, token=sign_in(booking.poster).access_token)

    assert error_codes(body) == ["RATE_LIMITED"]
    assert gateway.retrieves == []


@pytest.mark.django_db
def test_an_expired_checkout_is_closed_even_before_the_job_ends_it(
    client: Client, gateway: FakeGateway
) -> None:
    booking = opened(gateway)
    moved_back(booking, 31)

    assert error_codes(query(client, SESSION, booking, booking.poster)) == ["CHECKOUT_CLOSED"]
    assert gateway.retrieves == []


@pytest.mark.django_db
def test_a_paid_or_failed_checkout_is_closed(client: Client, gateway: FakeGateway) -> None:
    booking = opened(gateway)
    offers.withdraw_offer(booking.helper, str(booking.offer_id))

    assert error_codes(query(client, SESSION, booking, booking.poster)) == ["CHECKOUT_CLOSED"]


@pytest.mark.django_db
def test_a_payment_intent_cancelled_at_stripe_is_closed(
    client: Client, gateway: FakeGateway
) -> None:
    booking = opened(gateway)
    gateway.intent_status = "canceled"

    assert error_codes(query(client, SESSION, booking, booking.poster)) == ["CHECKOUT_CLOSED"]


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["processing", "succeeded"])
def test_a_payment_stripe_already_has_is_pending(
    client: Client, gateway: FakeGateway, status: str
) -> None:
    booking = opened(gateway)
    gateway.intent_status = status

    session = query(client, SESSION, booking, booking.poster)["data"]["checkoutSession"]

    # Still AWAITING_PAYMENT: only the webhook makes it FUNDS_HELD (hard rule a).
    assert session["paymentPending"] is True
    assert session["booking"]["status"] == "AWAITING_PAYMENT"


@pytest.mark.django_db
def test_stripe_failing_is_payment_unavailable(client: Client, gateway: FakeGateway) -> None:
    booking = opened(gateway)
    gateway.retrieve_error = StripeRequestError("payment_intents.retrieve", http_status=503)

    assert error_codes(query(client, SESSION, booking, booking.poster)) == ["PAYMENT_UNAVAILABLE"]


# bookingUpdated over the WebSocket.

SUBSCRIPTION = """
subscription($id: ID!) {
  bookingUpdated(id: $id) { id status lastPaymentAttempt { kind declineCode } }
}
"""


async def subscribe(
    user: AbstractBaseUser, booking_id: str, document: str = SUBSCRIPTION
) -> WebsocketCommunicator:
    """A signed-in socket that has sent the bookingUpdated subscription."""
    from config.asgi import application

    token = await sync_to_async(access_token)(user)
    ws = WebsocketCommunicator(application, "/graphql", subprotocols=["graphql-transport-ws"])
    assert (await ws.connect())[0]
    await ws.send_json_to({"type": "connection_init", "payload": {"authToken": token}})
    assert await ws.receive_json_from(timeout=5) == {"type": "connection_ack"}
    await ws.send_json_to(
        {
            "id": "1",
            "type": "subscribe",
            "payload": {"query": document, "variables": {"id": booking_id}},
        }
    )
    return ws


async def subscribed(
    user: AbstractBaseUser, booking_id: str, document: str = SUBSCRIPTION
) -> WebsocketCommunicator:
    """`subscribe`, once the subscription listens to the user's group."""
    ws = await subscribe(user, booking_id, document)
    layer = get_channel_layer()
    assert layer is not None
    for _ in range(100):
        if layer.groups.get(user_group(user.pk)):  # pyright: ignore[reportAttributeAccessIssue]
            return ws
        await asyncio.sleep(0.02)
    raise AssertionError("The subscription never joined the user's group.")


def update(booking_id: str, status: str, attempt: dict[str, Any] | None = None) -> dict:
    return {
        "id": "1",
        "type": "next",
        "payload": {
            "data": {
                "bookingUpdated": {
                    "id": booking_id,
                    "status": status,
                    "lastPaymentAttempt": attempt,
                }
            }
        },
    }


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> FakeGateway:
    """Transactional tests: real commits, so the WebSocket's own connection sees the rows."""
    fake = FakeGateway()
    monkeypatch.setattr(checkout, "get_gateway", lambda: fake)
    return fake


@pytest.mark.django_db(transaction=True)
def test_the_poster_sees_a_decline_live_and_others_get_nothing(live: FakeGateway) -> None:
    booking = opened(live)
    stranger = make_user("stranger")
    declined(booking)
    row = Outbox.objects.get(topic=checkout.PAYMENT_ATTEMPT_FAILED_TOPIC)
    poster = booking.poster

    async def run() -> None:
        poster_ws = await subscribed(poster, str(booking.pk))
        stranger_ws = await subscribe(stranger, str(booking.pk))

        # Refused at connect (hard rule d): not their booking.
        refusal = await stranger_ws.receive_json_from(timeout=5)
        assert refusal["payload"]["data"] is None
        assert [e["extensions"]["code"] for e in refusal["payload"]["errors"]] == [
            "BOOKING_NOT_FOUND"
        ]
        assert (await stranger_ws.receive_json_from(timeout=5))["type"] == "complete"

        await sync_to_async(send_to_users)(row, [booking.poster_id, stranger.pk])

        assert await poster_ws.receive_json_from(timeout=5) == update(
            str(booking.pk),
            "AWAITING_PAYMENT",
            {"kind": "DECLINED", "declineCode": "insufficient_funds"},
        )
        assert await stranger_ws.receive_nothing(timeout=0.3)
        await poster_ws.disconnect()
        await stranger_ws.disconnect()

    async_to_sync(run)()


@pytest.mark.django_db(transaction=True)
def test_the_helper_sees_status_changes_without_the_card_attempts(live: FakeGateway) -> None:
    booking = opened(live)
    declined(booking)
    offers.withdraw_offer(booking.helper, str(booking.offer_id))
    row = Outbox.objects.filter(topic="booking.updated").latest("pk")
    helper = booking.helper

    async def run() -> None:
        helper_ws = await subscribed(helper, str(booking.pk))

        await sync_to_async(send_to_users)(row, [booking.helper_id])

        assert await helper_ws.receive_json_from(timeout=5) == update(
            str(booking.pk), "PAYMENT_FAILED"
        )
        await helper_ws.disconnect()

    async_to_sync(run)()
