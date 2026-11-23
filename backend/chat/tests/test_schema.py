"""Chat over HTTP at /graphql: full objects back (rule d), participants only, masking before
acceptance, PHONE_NOT_VERIFIED and the rate limit (rule d)."""

from typing import Any

import pytest
from django.core.cache import caches
from django.test import Client

from accounts.models import User
from accounts.tests.factories import sign_in
from accounts.tests.test_schema import error_codes, gql
from bookings.models import Booking
from chat.tests.test_services import PHONE, booked_conversation, offer_conversation
from offers.tests.factories import make_helper

pytestmark = pytest.mark.django_db

MESSAGE = "id conversationId body senderRole isMine masked createdAt"
CONVERSATION = "id taskId taskTitle myRole counterpartName stage canSend bookingId lastMessageAt"
OPEN = f"mutation($offerId: ID!) {{ openConversation(offerId: $offerId) {{ {CONVERSATION} }} }}"
SEND = f"""
mutation($conversationId: ID!, $body: String!) {{
  sendMessage(conversationId: $conversationId, body: $body) {{ {MESSAGE} }}
}}
"""
GET = f"query($id: ID!) {{ conversation(id: $id) {{ {CONVERSATION} }} }}"
MESSAGES = f"""
query($conversationId: ID!) {{
  conversationMessages(conversationId: $conversationId) {{ {MESSAGE} }}
}}
"""


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> None:
    caches["graphql_rate_limit"].clear()


def token(user: User) -> str:
    return sign_in(user).access_token


def send(client: Client, user: User, conversation_id: Any, body: str) -> dict[str, Any]:
    return gql(client, SEND, {"conversationId": str(conversation_id), "body": body}, token(user))


def test_opening_returns_the_full_conversation(client: Client) -> None:
    offer, _ = offer_conversation()
    User.objects.filter(pk=offer.helper_id).update(first_name="Marko", last_name="Perić")

    body = gql(client, OPEN, {"offerId": str(offer.pk)}, token(offer.task.poster))

    conversation = body["data"]["openConversation"]
    assert conversation["taskId"] == str(offer.task_id)
    assert conversation["taskTitle"] == offer.task.title
    assert conversation["myRole"] == "POSTER"
    assert conversation["counterpartName"] == "Marko P."
    assert (conversation["stage"], conversation["canSend"]) == ("OFFER", True)
    assert conversation["bookingId"] is None


def test_a_message_before_acceptance_comes_back_masked(client: Client) -> None:
    offer, conversation = offer_conversation()

    body = send(client, offer.helper, conversation.pk, PHONE)

    message = body["data"]["sendMessage"]
    assert message["conversationId"] == str(conversation.pk)
    assert (message["senderRole"], message["isMine"], message["masked"]) == ("HELPER", True, True)
    assert "5678" not in message["body"]


def test_a_message_after_acceptance_comes_back_as_written(client: Client) -> None:
    booking, conversation = booked_conversation(Booking.Status.FUNDS_HELD)

    body = send(client, booking.helper, conversation.pk, PHONE)

    assert body["data"]["sendMessage"]["body"] == PHONE
    assert body["data"]["sendMessage"]["masked"] is False
    got = gql(client, GET, {"id": str(conversation.pk)}, token(booking.poster))
    assert got["data"]["conversation"]["stage"] == "BOOKED"
    assert got["data"]["conversation"]["bookingId"] == str(booking.pk)


def test_both_participants_read_the_messages_and_nobody_else(client: Client) -> None:
    offer, conversation = offer_conversation()
    send(client, offer.helper, conversation.pk, "Dobar dan")
    variables = {"conversationId": str(conversation.pk)}

    poster_view = gql(client, MESSAGES, variables, token(offer.task.poster))
    stranger = gql(client, MESSAGES, variables, token(make_helper("ivan")))

    [message] = poster_view["data"]["conversationMessages"]
    assert (message["body"], message["isMine"], message["senderRole"]) == (
        "Dobar dan",
        False,
        "HELPER",
    )
    assert error_codes(stranger) == ["CONVERSATION_NOT_FOUND"]
    assert error_codes(gql(client, GET, {"id": str(conversation.pk)})) == ["UNAUTHENTICATED"]


def test_sending_without_a_verified_phone_is_refused(client: Client) -> None:
    offer, conversation = offer_conversation()
    User.objects.filter(pk=offer.task.poster_id).update(phone_verified_at=None)

    body = send(client, offer.task.poster, conversation.pk, "Hi")

    assert error_codes(body) == ["PHONE_NOT_VERIFIED"]


def test_a_doubtful_message_is_refused_with_its_code(client: Client) -> None:
    offer, conversation = offer_conversation()

    body = send(client, offer.helper, conversation.pk, "091\n234\n5678")

    assert error_codes(body) == ["MESSAGE_CONTACT_DETAILS"]


def test_sending_is_rate_limited(client: Client, settings: Any) -> None:
    settings.GRAPHQL_MUTATION_RATE_LIMIT_OVERRIDES = {"sendMessage": 2}
    offer, conversation = offer_conversation()

    send(client, offer.helper, conversation.pk, "1")
    send(client, offer.helper, conversation.pk, "2")

    assert error_codes(send(client, offer.helper, conversation.pk, "3")) == ["RATE_LIMITED"]
