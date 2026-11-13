"""The booking detail screen's data (BEAV-115): the booked task inside `booking` and
`bookingUpdated`, with the exact place only for the poster and for the helper while the
booking is active (hard rule e, `bookings.queries.active`), and a trader helper's name and
OIB for the poster as confirmed at checkout (BEAV-192). Its registered business address
stays on the offer and the checkout (rule e): the booking never carries it."""

import json
from typing import Any

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from django.test import Client

from accounts.models import User
from accounts.tests.factories import access_token, make_user
from accounts.tests.test_schema import error_codes, gql
from bookings import no_fault, state_machine
from bookings.cancellation import NoFaultReason
from bookings.models import Booking
from bookings.tests.factories import make_booking
from bookings.tests.fakes import FakeGateway
from bookings.tests.test_checkout import (  # noqa: F401 (fixtures)
    _fresh_rate_limits,
    legal_documents,
    webhook_secret,
)
from bookings.tests.test_checkout_session import subscribe, subscribed
from bookings.tests.test_poster_cancel import paid
from care_contacts.tests.factories import make_contact
from core.models import ActorKind, Outbox
from core.realtime import send_to_users
from tasks.tests.factories import make_poster

pytestmark = pytest.mark.django_db

S = Booking.Status
EXACT = ("address", "lat", "lng", "accessDetails", "onSiteContact")
TASK = (
    "task { id title approximateLat address lat lng accessDetails onSiteContact { id name phone } }"
)
BOOKING = f"query($id: ID!) {{ booking(id: $id) {{ id status {TASK} }} }}"
BUSINESS = "helperBusiness { id businessName businessOib }"
TRADER = f"query($id: ID!) {{ booking(id: $id) {{ id {BUSINESS} }} }}"
TRADER_ADDRESS = "Obala Stjepana Radića 12, 20000 Dubrovnik, HR"


def booked(status: S = S.FUNDS_HELD) -> Booking:
    poster = make_poster("ana")
    contact = make_contact(poster)
    return make_booking(status, poster=poster, care_contact_id=str(contact.pk))


def task_seen_by(client: Client, booking: Booking, viewer: User) -> dict[str, Any]:
    body = gql(client, BOOKING, {"id": str(booking.pk)}, access_token(viewer))
    assert "errors" not in body, error_codes(body)
    return body["data"]["booking"]["task"]


def hidden(task: dict[str, Any]) -> bool:
    return all(task[field] is None for field in EXACT)


def test_the_booking_carries_its_task(client: Client) -> None:
    booking = booked()

    task = task_seen_by(client, booking, booking.helper)

    assert task["id"] == str(booking.task_id)
    assert task["title"]
    assert task["approximateLat"] is not None


def test_the_helper_of_an_active_booking_sees_the_exact_place(client: Client) -> None:
    booking = booked()

    task = task_seen_by(client, booking, booking.helper)

    assert task["address"] == "Ulica Ante Starčevića 1, Dubrovnik"
    assert task["accessDetails"] == "Gate code 1234, key under the mat"
    assert task["onSiteContact"]["phone"] == "+385912345678"


@pytest.mark.parametrize(
    "status", [s for s in S if s not in Booking.ACTIVE or s == S.CANCEL_REQUESTED]
)
def test_the_helper_sees_only_the_area_outside_an_active_booking(client: Client, status: S) -> None:
    """Before the payment is confirmed, after a recorded cancel or no-show report, and once
    the booking closes; the poster still sees their own task's place."""
    booking = booked(status)

    assert hidden(task_seen_by(client, booking, booking.helper))
    assert not hidden(task_seen_by(client, booking, booking.poster))


def test_the_address_is_hidden_again_once_the_booking_closes(client: Client) -> None:
    booking = booked(S.PAYMENT_REQUESTED)
    assert not hidden(task_seen_by(client, booking, booking.helper))

    state_machine.transition(
        booking.pk, S.RELEASED, actor_kind=ActorKind.POSTER, actor=booking.poster
    )
    assert hidden(task_seen_by(client, booking, booking.helper))

    # Paid out and closed later by Stripe's webhook and the review window's sweep.
    for status in (S.PAID_OUT, S.CLOSED):
        Booking.objects.filter(pk=booking.pk).update(status=status)
        assert hidden(task_seen_by(client, booking, booking.helper)), status


def test_a_pending_no_fault_request_keeps_the_place_until_it_is_answered(
    client: Client,
) -> None:
    booking = booked()
    no_fault.request_no_fault(booking.poster, booking.pk, NoFaultReason.WEATHER)
    booking.refresh_from_db()
    assert booking.status == S.CANCEL_REQUESTED

    assert not hidden(task_seen_by(client, booking, booking.helper))


def test_nobody_else_gets_the_booking(client: Client) -> None:
    booking = booked()

    body = gql(client, BOOKING, {"id": str(booking.pk)}, access_token(make_user("eve")))

    assert error_codes(body) == ["BOOKING_NOT_FOUND"]
    assert body["data"] is None


# A registered-business helper: what the poster saw on the offer and at checkout.


def trader_seen_by(client: Client, booking: Booking, viewer: User) -> dict[str, Any] | None:
    body = gql(client, TRADER, {"id": str(booking.pk)}, access_token(viewer))
    assert "errors" not in body, error_codes(body)
    return body["data"]["booking"]["helperBusiness"]


def test_the_poster_sees_the_traders_name_and_oib_as_confirmed_at_checkout(
    client: Client, gateway: FakeGateway
) -> None:
    booking = paid(client, trader=True)
    assert booking.consent.trader_address == TRADER_ADDRESS

    assert trader_seen_by(client, booking, booking.poster) == {
        "id": str(booking.pk),
        "businessName": "Vrtlarski obrt Horvat",
        "businessOib": booking.consent.trader_oib,
    }


@pytest.mark.parametrize(
    "document",
    [
        "query($id: ID!) { booking(id: $id) { id helperBusiness { businessAddress } } }",
        "subscription($id: ID!) { bookingUpdated(id: $id) { id helperBusiness "
        "{ businessAddress } } }",
    ],
)
def test_the_booking_has_no_business_address_to_ask_for(client: Client, document: str) -> None:
    """Rule e: the registered address is for the offer and the checkout, not the booking,
    in the detail query and in bookingUpdated alike."""
    booking = booked()

    body = gql(client, document, {"id": str(booking.pk)}, access_token(booking.poster))

    assert body.get("data") is None
    assert "Cannot query field 'businessAddress'" in body["errors"][0]["message"]


def test_the_helper_and_a_private_helpers_poster_get_no_business(
    client: Client, gateway: FakeGateway
) -> None:
    trader = paid(client, trader=True)
    private = paid(client, helpers=("luka", "petar"))

    assert trader_seen_by(client, trader, trader.helper) is None
    assert trader_seen_by(client, private, private.poster) is None


# bookingUpdated: each event is the booking as the viewer may see it at that moment.


@pytest.mark.django_db(transaction=True)
def test_the_live_event_gives_the_poster_the_traders_name_without_its_address(
    client: Client, gateway: FakeGateway
) -> None:
    booking = paid(client, trader=True)
    poster = booking.poster
    oib = booking.consent.trader_oib
    row = Outbox.objects.filter(
        topic=state_machine.UPDATED_TOPIC, aggregate_id=str(booking.pk)
    ).latest("pk")
    document = f"subscription($id: ID!) {{ bookingUpdated(id: $id) {{ id {BUSINESS} }} }}"

    async def run() -> None:
        ws = await subscribed(poster, str(booking.pk), document=document)

        await sync_to_async(send_to_users)(row, [poster.pk])

        message = await ws.receive_json_from(timeout=5)
        assert message["payload"]["data"]["bookingUpdated"]["helperBusiness"] == {
            "id": str(booking.pk),
            "businessName": "Vrtlarski obrt Horvat",
            "businessOib": oib,
        }
        assert "Obala Stjepana" not in json.dumps(message, ensure_ascii=False)
        await ws.disconnect()

    async_to_sync(run)()


@pytest.mark.django_db(transaction=True)
def test_the_live_event_that_closes_the_booking_hides_the_address() -> None:
    booking = booked(S.PAYMENT_REQUESTED)
    helper = booking.helper
    state_machine.transition(
        booking.pk, S.RELEASED, actor_kind=ActorKind.POSTER, actor=booking.poster
    )
    row = Outbox.objects.filter(topic=state_machine.UPDATED_TOPIC).latest("pk")

    async def run() -> None:
        ws = await subscribed(helper, str(booking.pk), document=LIVE)

        await sync_to_async(send_to_users)(row, [helper.pk])

        message = await ws.receive_json_from(timeout=5)
        event = message["payload"]["data"]["bookingUpdated"]
        assert event["status"] == "RELEASED"
        assert hidden(event["task"])
        await ws.disconnect()

    async_to_sync(run)()


@pytest.mark.django_db(transaction=True)
def test_a_live_event_of_an_active_booking_keeps_the_place_for_the_helper() -> None:
    booking = booked(S.FUNDS_HELD)
    helper = booking.helper
    state_machine.transition(
        booking.pk, S.PAYMENT_REQUESTED, actor_kind=ActorKind.HELPER, actor=helper
    )
    row = Outbox.objects.filter(topic=state_machine.UPDATED_TOPIC).latest("pk")

    async def run() -> None:
        ws = await subscribed(helper, str(booking.pk), document=LIVE)
        stranger = await sync_to_async(make_user)("stranger")
        stranger_ws = await subscribe(stranger, str(booking.pk), document=LIVE)
        refusal = await stranger_ws.receive_json_from(timeout=5)
        assert [e["extensions"]["code"] for e in refusal["payload"]["errors"]] == [
            "BOOKING_NOT_FOUND"
        ]
        assert (await stranger_ws.receive_json_from(timeout=5))["type"] == "complete"

        await sync_to_async(send_to_users)(row, [helper.pk, stranger.pk])

        event = (await ws.receive_json_from(timeout=5))["payload"]["data"]["bookingUpdated"]
        assert event["task"]["address"] == "Ulica Ante Starčevića 1, Dubrovnik"
        assert await stranger_ws.receive_nothing(timeout=0.3)
        await ws.disconnect()
        await stranger_ws.disconnect()

    async_to_sync(run)()


LIVE = f"subscription($id: ID!) {{ bookingUpdated(id: $id) {{ id status {TASK} }} }}"
